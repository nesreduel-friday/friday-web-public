"""Friday-Kern — Einstieg.

Phase 1: Sprechen (WebSocket /ws/live) und Chat (POST /api/chat).
"""

import asyncio
import hmac
import time
import json
import logging
import mimetypes
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import FastAPI, Form, HTTPException, Query, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool

from kern import config, erinnerungen, konnektoren, schluessel as schluesselstand, umgebung
from kern import medien as mediendienst
from kern.dienste import standort as standortdienst, whatsapp as whatsappdienst
from kern.regeln import speicher as regelspeicher_api, zeit as regelzeit
from kern.aufgabenblatt import ausarbeitung, planer as aufgabenplaner, speicher as aufgabenspeicher
from kern.recherche import planer as rechercheplaner
from kern.dienste import benachrichtigungen, wache
from kern.dienste import radarr as radarrdienst, sonarr as sonarrdienst
from kern.dashboard import friday_karten
from kern.dashboard import minecraft as minecraftkarte
from kern.dashboard import jellyfin as dashboardjellyfin
from kern.dashboard import pcmedien as dashboardpcmedien
from kern.dashboard import karten as dashboardkarten, musik as dashboardmusik
from kern.dienste import pc as pcdienst
from kern.dienste import claude_sitzung
from kern.dienste import hphome as hphomedienst
from kern.dienste import minecraft_bot as minecraftbotdienst
from kern.dienste import container_doku as containerdoku
from kern.dienste import hphome_wache, snapchat_wache
from kern.dienste import snapchat as snapchatdienst
from kern.dienste import prozesse
from kern.dienste import schluessel_pool
from kern.dienste import gmail, icloud, iserv_mail, push, schulmanager, spotify, studio, webdav
from kern.dienste import bildsuche
from kern.dienste import homeassistant as hadienst
from kern.dienste import personenstandort as personendienst
from kern.personen import PersonenFehler, flammen as personenflammen, module as personenmodule, uebersicht as personenuebersicht, bekannt as personenbekannt, freigaben as personenfreigaben, rueckfragen as personenrueckfragen, rechte as personenrechte, register as personenregister, quellen as personenquellen, unbekannt as personenunbekannt
from kern.dienste import jellyfin as jellyfindienst
from kern.snapmem import auftraege as snapmemdienst
from kern.fokus import ablage as fokusablage, sammler as fokussammler
from kern.gedaechtnis import notizen as wissensnotizen
from kern.gedaechtnis import antwort as wissensantwort, bilder, graph, rueckblick, seite as wissensseite, themen as wissensthemen, wissensgraph
from kern.dienste import wetter
from kern.planer import briefing
from kern.denken import buch as gedankenbuch
from kern.fragen import fach as fragenfach
from kern.denken import lauf as denkenlauf
from kern.denken import runde as denkrunde
from kern.werkbank import lauf as werkbanklauf, speicher as werkbankspeicher, starten as werkbankstarter
from kern.werkbank import uebernehmen as werkbankuebernahme   # nur hier: Live-Gang allein per Knopf
from kern.werkbank import selbst as werkbankselbst           # nur hier: Eikes Hebel „selbst übernehmen“
from kern.werkbank import automatik as werkbankautomatik      # nur hier: Eikes Hebel „Automatik“ (Route + Dauerlauf)
from kern.werkzeuge import wetter as wetterwerkzeug
from kern.dienste import garantie as garantiedienst
from kern.garantie import ablage as garantieablage
from kern.garantie import bahnablage, bahnformular, fahrgastrechte
from kern.garantie import abschicken as garantieabschicken
from kern.garantie import antrag as garantieantrag
from kern.garantie import suche as garantiesuche
from kern.garantie import ticket as garantieticket
from kern.garantie import uebernahme as garantieuebernahme
from kern.garantie import post as garantiepost
from kern.garantie import profile as garantieprofile
from kern.garantie import sammler as garantiesammler
from kern.garantie import uebersicht as garantieuebersicht
from kern.garantie import verwaltung as garantieverwaltung
from kern.gedaechtnis import speicher as gspeicher
from kern.chatstrang import speicher as chatstrang
from kern.protokoll import speicher
from kern.chats import ablage as chatablage
from kern.chats import auswertung as chat_auswertung
from kern.chats import einlesen as chat_einlesen
from kern.postfach import ablage as postablage
from kern.postfach import abgleich
from kern.schule import ablage, briefe
from kern.goodnotes import abgleich as goodnotesabgleich
from kern.goodnotes import ablage as goodnotesablage
from kern.schule import buecher as buecherablage
from kern.schule import gruppenordner as gruppenordnerablage
from kern.schule import hausaufgaben as hausaufgabenablage
from kern.schule import arbeiten as arbeitenablage
from kern.schule import lernen as lernenablage
from kern.schule import noten as notenablage
from kern.sitzung import chat, chatmodell, live
from kern.werkzeuge import kalender as kalenderwerkzeug
from kern.werkzeuge import musik as musikwerkzeug
from kern.werkzeuge import gmail as gmailwerkzeug
from kern.studio import planer as studioplaner
from kern.studio import statistik as studiostatistik
from kern.werkzeuge import studio as studiowerkzeug
from kern.werkzeuge import fahrplan, post, schule
from kern.werkzeuge import pc as pcwerkzeug
from kern.werkzeuge import dokument as dokumentwerkzeug

# Python kennt .webmanifest nicht auf jedem System. Mit falschem Typ
# ignorieren manche Browser das Manifest — dann lässt sich Friday nicht
# als App installieren, ohne dass irgendwo ein Fehler erscheint.
mimetypes.add_type("application/manifest+json", ".webmanifest")

WEB = Path(__file__).resolve().parent.parent / "web"

# Was Friday neu anlegt (Datenbanken, Sicherungen, Zustandsdateien), gehört nur ihm.
# Vorher 644: Mail-, Schul- und Gedächtnis-Datenbanken waren für jeden lokalen Benutzer
# von hphome lesbar, obwohl die AGENTS.md 600 zusagt.
os.umask(0o077)
log = logging.getLogger("friday.main")

# Keine öffentlichen API-Docs: das ist Eikes Assistent, keine Schnittstelle.
# openapi_url extra abschalten — docs_url/redoc_url allein lassen die
# komplette Schnittstellenbeschreibung unter /openapi.json offen.
@asynccontextmanager
async def lebenszyklus(_app):
    # Der Rückblick läuft im Hintergrund: zusammenfassen, ins Langzeitgedächtnis,
    # nachsehen, nachts sichern. FRIDAY_HINTERGRUND=aus schaltet ihn ab.
    # Dazu die Elternbriefe: verarbeitet, sobald sie ankommen.
    aufgaben = []
    def _starten(name: str, coro) -> asyncio.Task:
        prozesse.gestartet(name)
        t = asyncio.create_task(coro)
        def _beendet(task: asyncio.Task):
            if not task.cancelled():
                if exc := task.exception():
                    log.error("Hintergrund-Aufgabe %s unerwartet beendet: %s", name, exc, exc_info=exc)
                    prozesse.abgestuerzt(name, f"{type(exc).__name__}: {exc}")
        t.add_done_callback(_beendet)
        return t

    # Kontakt-Module (14.10.2026): Personen ohne Feld `module` bekommen es aus ihrem Bestand — einmalig, idempotent.
    try:
        try:
            from kern.regeln import speicher as regelspeicher
            if n := regelspeicher.laufende_abbrechen():
                log.warning("Regeln: %d unterbrochene Läufe als Fehler festgehalten", n)
        except Exception:  # noqa: BLE001 — der Start darf daran nie scheitern
            log.exception("Regeln: Aufräumen beim Start gescheitert")
        if n := personenmodule.migrieren():
            log.info("Kontakt-Module: %s Personen migriert", n)
    except Exception:
        log.exception("Kontakt-Module: Migration gescheitert")

    # Verwaiste leere Upload-Ordner bei Buchscans aufräumen
    try:
        eingang_basis = buecherablage.standard().ordner / "schule" / "buecher" / "eingang"
        if eingang_basis.exists():
            for alt in eingang_basis.iterdir():
                if alt.is_dir() and not any(alt.iterdir()):
                    try:
                        alt.rmdir()
                    except OSError:
                        pass
    except Exception:
        pass

    if os.getenv("FRIDAY_HINTERGRUND", "an").strip().lower() != "aus":
        aufgaben = [_starten("rueckblick", rueckblick.dauerlauf()),
                    # Alle 2 Stunden still nachdenken: lesen, verknüpfen, Gedanken festhalten, selten etwas melden.
                    _starten("denken", denkenlauf.dauerlauf()),
                    # Werkbank: Stand laufender Vorhaben alle 30 s lesen, Ergebnisse eintragen und einmal melden.
                    _starten("werkbank", werkbanklauf.dauerlauf()),
                    # Werkbank-Automatik: nur bei Eikes Hebel „an“ alle 5 min das älteste passende Vorhaben starten.
                    _starten("werkbank_automatik", werkbankautomatik.dauerlauf()),
                    # Die wichtigsten Wissensseiten vorbauen, damit der erste Besuch nicht auf das Modell wartet.
                    _starten("wissensseiten", wissensseite.vorwaermen()),
                    # Fokus-Personen: alle paar Stunden Neues aus WhatsApp und Gmail sammeln (nur freigegebene Quellen, nur lesen).
                    _starten("fokus", fokussammler.dauerlauf()),
                    _starten("briefe", briefe.dauerlauf()),
                    _starten("abgleich", abgleich.dauerlauf()),
                    _starten("chat_auswertung", chat_auswertung.dauerlauf()),
                    _starten("erinnerungen", erinnerungen.dauerlauf()),
                    # Flammen (14.10.2026): ein täglicher Snap an Personen mit dem Hebel „flammen“. Schalter aus, bis Eike ihn umlegt.
                    _starten("flammen", personenflammen.dauerlauf()),
                    # Ein Video pro Tag planen und melden, wenn eines fertig ist.
                    # Tut nichts, solange das Studio nicht freigegeben ist.
                    _starten("studioplaner", studioplaner.dauerlauf()),
                    # Einmal am Morgen: Wetter, Schultag, Termine. Aus, bis freigegeben.
                    _starten("briefing", briefing.dauerlauf()),
                    # Verspätete Stadtbahnen und Busse für die ÜSTRA-Garantie. Aus, bis freigegeben.
                    _starten("garantiesammler", garantiesammler.dauerlauf()),
                    # Bahn-Fahrgastrechte: Ankunft nachmessen, bereite Anträge melden. Aus, bis freigegeben.
                    _starten("fahrgastrechte", fahrgastrechte.dauerlauf()),
                    # Neue Hausaufgaben zerlegen und auf Machbarkeit prüfen (Extras → HA).
                    _starten("hausaufgabenablage", hausaufgabenablage.dauerlauf()),
                    # Goodnotes-Hefte aus dem IServ-WebDAV abgleichen und abschreiben.
                    _starten("goodnotesabgleich", goodnotesabgleich.dauerlauf()),
                    # Gruppenordner (Kursmaterialien) aus dem IServ-WebDAV abgleichen und zusammenfassen.
                    _starten("gruppenordner", gruppenordnerablage.dauerlauf()),
                    # Unterbrochen gelesene Bücher fortsetzen, neue lesen, sobald freigegeben.
                    _starten("buecherablage", buecherablage.fortsetzen()),
                    # Beim Lesen unterbrochene Klassenarbeiten fortsetzen.
                    _starten("arbeitenablage", arbeitenablage.fortsetzen()),
                    # Sieht stündlich in den freigegebenen WhatsApp-Chats nach —
                    # gestreut, nur tagsüber, abschaltbar über WHATSAPP_HINTERGRUND=aus.
                    _starten("whatsapp", whatsappdienst.dauerlauf()),
                    _starten("whatsapp_regeln", whatsappdienst.regel_dauerlauf()),
                    _starten("regeln_zeit", regelzeit.dauerlauf()),
                    _starten("recherche", rechercheplaner.dauerlauf()),
                    _starten("aufgabenblatt", aufgabenplaner.dauerlauf()),
                    # Meldet, wenn die Platte fast voll ist oder ein Dienst auf hphome ausfällt.
                    _starten("hphome_wache", hphome_wache.dauerlauf()),
                    # Erkennt Eikes Antworten auf Snaps und lässt Friday normal weiterreden.
                    _starten("snapchat_wache", snapchat_wache.dauerlauf()),
                    # Sieht nach, ob eine gemerkte Abfahrt Verspätung hat — nur, solange
                    # ein Zeitfenster offen ist, sonst alle zehn Minuten kurz.
                    _starten("wache", wache.dauerlauf())]
    yield
    for aufgabe in aufgaben:
        aufgabe.cancel()


app = FastAPI(title="Friday", version=config.VERSION, lifespan=lebenszyklus,
              docs_url=None, redoc_url=None, openapi_url=None)


_ANDERT_ETWAS = {"POST", "PUT", "PATCH", "DELETE"}


_LOKAL_HERKUNFT = {("http", "127.0.0.1", 8124), ("http", "localhost", 8124), ("http", "::1", 8124)}


def _herkunft(url: str) -> tuple[str, str, int] | None:
    """(Schema, Host, Port) einer reinen Herkunft `schema://host[:port]` — sonst None (Userinfo, Pfad, Abfrage …)."""
    try:
        p = urlparse(url)
        port = p.port
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname or p.username is not None or p.password is not None \
            or p.path not in ("", "/") or p.params or p.query or p.fragment:
        return None
    return p.scheme, p.hostname.lower(), port or (443 if p.scheme == "https" else 80)


def _origin_ist_friday(origin: str) -> bool:
    """Streng: Schema, Host UND Port müssen genau Friday sein (`FRIDAY_URL`) oder der lokale Zugang auf 8124.
    Auf demselben Hostnamen laufen Sonarr, Jellyfin & Co. — deren Seiten dürfen hier nichts auslösen.
    `live.origin_erlaubt` (nur Hostname) bleibt für den WebSocket; hier geht es um ändernde Anfragen."""
    if origin == "testserver":                             # Starlette-TestClient (kein Browser schickt das)
        return True
    herkunft = _herkunft(origin)
    if herkunft is None or origin.endswith("/"):           # ein Browser schickt nie einen Pfad, auch kein „/“
        return False
    return herkunft in _LOKAL_HERKUNFT or herkunft == _herkunft(config.basis_url().rstrip("/"))


def _fremde_seite(request: Request) -> bool:
    """Löst eine fremde Webseite in Eikes Browser diese Anfrage aus (CSRF)?

    Es gibt keine Anmeldung — Friday vertraut dem Tailnet. Ohne diese Prüfung
    könnte jede Seite, die Eike besucht, per Formular-POST ohne Body Knöpfe
    drücken (Bücher löschen, Konnektoren trennen). Browser schicken bei jedem
    Formular-POST `Origin`; `Sec-Fetch-Site` fängt den Rest. Werkzeuge ohne
    beides (curl, Tests) sind keine Browser-Angriffe und bleiben erlaubt."""
    if request.method not in _ANDERT_ETWAS:
        return False
    origin = request.headers.get("origin")
    if origin is not None:
        return not _origin_ist_friday(origin)
    return request.headers.get("sec-fetch-site") in ("cross-site", "same-site")


# Was im Speicher landet, bevor die Route es überhaupt sieht: JSON wird ganz gelesen und
# geprüft. Hochladen (multipart) geht auf die Platte und darf groß sein (Bücher: PDFs bis 300 MB).
MAX_JSON_B = 64 * 1024 * 1024
MAX_MULTIPART_B = 1536 * 1024 * 1024


def _zu_gross(request: Request) -> bool:
    if request.method not in _ANDERT_ETWAS:
        return False
    try:
        laenge = int(request.headers.get("content-length", "0"))
    except ValueError:
        return True
    multipart = request.headers.get("content-type", "").lower().startswith("multipart/")
    return laenge > (MAX_MULTIPART_B if multipart else MAX_JSON_B)


def _csp(host: str) -> str:
    """Nur Fridays eigene Skripte laufen. Eingeschleustes HTML (aus Mails, Briefen,
    Dateien) kann dann nichts ausführen und keine Daten per Skript oder Verbindung nach
    außen schicken. Bilder dürfen von https:-Adressen kommen: YouTube-Vorschaubilder im
    Studio und Spotify-Cover laden direkt vom Anbieter (am 26.09.2026 live aufgefallen —
    die leere Testinstanz hatte sie nie gezeigt)."""
    hosts = {h for h in (host, urlparse(config.basis_url()).netloc) if re.fullmatch(r"[A-Za-z0-9.\-:\[\]]{1,255}", h or "")}
    ws = " ".join(f"{s}://{h}" for h in sorted(hosts) for s in ("ws", "wss"))
    return ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data: blob: https:; media-src 'self' blob:; connect-src 'self' {ws}; "
            "worker-src 'self'; manifest-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'; "
            "object-src 'none'")


@app.middleware("http")
async def frisch_halten(request: Request, call_next):
    if _fremde_seite(request):
        return JSONResponse({"detail": "Fremde Herkunft."}, status_code=403)
    if _zu_gross(request):
        return JSONResponse({"detail": "Die Anfrage ist zu groß."}, status_code=413)
    antwort = await call_next(request)
    # Überall frisch: Eine als App installierte Seite hängt auf iOS sonst gern am
    # alten Stand (auch mit no-cache blieb der Abläufe-Block weg). Bei einem
    # Einzel-Nutzer zuhause ist das Neuladen der (winzigen) Web-Dateien billig.
    antwort.headers["Cache-Control"] = "no-store"
    antwort.headers["X-Content-Type-Options"] = "nosniff"
    antwort.headers["X-Frame-Options"] = "SAMEORIGIN"
    antwort.headers["Referrer-Policy"] = "same-origin"
    antwort.headers["Permissions-Policy"] = "microphone=(self), camera=(self), geolocation=(self)"
    # Chromes PDF-Ansicht startet nicht unter `object-src 'none'` — PDFs bekommen nur nosniff.
    if not antwort.headers.get("content-type", "").startswith("application/pdf"):
        antwort.headers.setdefault("Content-Security-Policy", _csp(request.headers.get("host", "")))
    return antwort


@app.exception_handler(OverflowError)
async def zahl_zu_gross(request: Request, exc: OverflowError):
    """`/api/buecher/99999999999999999999` — SQLite kennt nur 64 Bit. Eine solche Nummer
    kann es nicht geben; das ist ein 404, kein Serverfehler."""
    if "SQLite" in str(exc):
        return JSONResponse({"detail": "Das gibt es nicht."}, status_code=404)
    raise exc


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": config.VERSION,
        "phase": config.PHASE,
        "stimmmodell": config.stimmmodell(),
        "textmodell": config.textmodell(),
        "stimme": config.stimme(),
        # Nur OB ein Schlüssel da ist — nie welcher.
        "geminiSchluessel": bool(config.gemini_schluessel()),
        # Wissensgraph: womit die Vektoren gerechnet werden (lokal oder gemini) — nie ein Schlüssel.
        "graph_embedder": config.graph_embedder(),
    }


class Bild(BaseModel):
    mime: Literal["image/jpeg", "image/png", "image/webp"]
    data: str = Field(max_length=8_000_000)


class Datei(BaseModel):
    mime: Literal["application/pdf"]
    name: str = Field("", max_length=200)
    data: str = Field(max_length=14_000_000)       # ~10 MB als Base64


class Nachricht(BaseModel):
    rolle: Literal["du", "friday"]
    text: str = Field("", max_length=20000)
    bild: Optional[Bild] = None
    datei: Optional[Datei] = None


class ChatAnfrage(BaseModel):
    nachrichten: list[Nachricht] = Field(min_length=1, max_length=60)
    gespraech: Optional[int] = None
    # Welche Oberfläche fragt? Sonst rät Friday bei „Wo bist du?" die zuletzt
    # gemeldete — am 13.09.2026 dreimal hintereinander verschieden.
    kennung: str = Field("", max_length=64)
    # Vom Browser vergeben: Verlässt Eike die App mitten in der Antwort, holt die Seite das Ergebnis danach über diese Id ab.
    anfrage_id: str = Field("", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    # Chat-Strang (28.10.2026): Der Verlauf liegt auf dem Server. Mit `strang` zählt vom Browser nur die LETZTE Nachricht;
    # den Kontext baut der Server aus dem Strang seit „Chat leeren“.
    strang: bool = False


_chat_laeufe: dict[str, dict] = {}      # anfrage_id -> {"zeit", "zustand": laeuft|fertig|fehler, "ergebnis"|"detail"}
_chat_aufgaben: set = set()
CHAT_ERGEBNIS_S = 900                   # so lange bleibt ein Ergebnis zum Abholen liegen


def _chat_laeufe_aufraeumen() -> None:
    grenze = time.time() - CHAT_ERGEBNIS_S
    for k in [k for k, v in _chat_laeufe.items() if v["zeit"] < grenze]:
        _chat_laeufe.pop(k, None)


@app.post("/api/chat")
async def api_chat(anfrage: ChatAnfrage) -> dict:
    """Die Arbeit läuft als eigene Aufgabe: Bricht der Browser ab (iOS schließt Verbindungen, sobald die App in den
    Hintergrund geht), antwortet Friday trotzdem zu Ende, speichert und legt das Ergebnis zum Abholen bereit."""
    if not config.gemini_schluessel():
        raise HTTPException(503, "Kein Gemini-Schlüssel hinterlegt.")
    if anfrage.nachrichten[-1].rolle != "du":
        raise HTTPException(400, "Die letzte Nachricht muss von dir sein.")
    _chat_laeufe_aufraeumen()
    if anfrage.anfrage_id:
        _chat_laeufe[anfrage.anfrage_id] = {"zeit": time.time(), "zustand": "laeuft"}
    aufgabe = asyncio.create_task(_chat_ausfuehren(anfrage))
    _chat_aufgaben.add(aufgabe)
    aufgabe.add_done_callback(_chat_aufgaben.discard)
    return await asyncio.shield(aufgabe)


@app.get("/api/aufgabenblatt/{aid}/ausarbeitung")
def aufgabenblatt_ausarbeitung(aid: int) -> Response:
    try:
        daten = ausarbeitung.bauen(aid)
    except aufgabenspeicher.AufgabenFehler as fehler:
        raise HTTPException(404, str(fehler)) from None
    return Response(daten, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="ausarbeitung-{aid}.pdf"', "Cache-Control": "no-store"})


@app.get("/api/dokumente/{dok_id}")
def api_dokument(dok_id: str) -> FileResponse:
    """Ein von Friday erstelltes PDF aus dem Chat — nur Lesen, deshalb bewusst ohne Origin-Prüfung."""
    try:
        pfad, name = dokumentwerkzeug.pfad_und_name(dok_id)
    except dokumentwerkzeug.DokumentFehler as fehler:
        raise HTTPException(404, str(fehler)) from None
    return FileResponse(pfad, media_type="application/pdf", filename=name, headers={"Cache-Control": "no-store"})


@app.get("/api/tokens")
async def api_tokens(tage: int = 1):
    """Gemini-Tokenverbrauch (nur Zahlen, nie Inhalte), je Modell und Bereich."""
    from kern.verbrauch import tokens
    return tokens.uebersicht(tage)


@app.get("/api/chat/strang")
def api_chat_strang(nach: int = 0) -> dict:
    """Was der Chat zeigt: alle Zeilen nach `nach` (und nach der Marke „Chat leeren“). Der Browser fragt damit nach."""
    strang = chatstrang.standard()
    return {"eintraege": strang.sichtbar(max(0, nach)), "letzte_id": strang.letzte_id(), "leer_ab": strang.leer_ab()}


@app.post("/api/chat/leeren")
def api_chat_leeren() -> dict:
    """„Chat leeren“: blendet den Verlauf auf allen Geräten aus und leert Fridays Kontext. Gelöscht wird nichts."""
    return {"ok": True, "leer_ab": chatstrang.standard().leeren()}


@app.get("/api/chat/ergebnis/{anfrage_id}")
def api_chat_ergebnis(anfrage_id: str, antwort: Response) -> dict:
    antwort.headers["Cache-Control"] = "no-store"
    eintrag = _chat_laeufe.get(anfrage_id)
    if eintrag is None:
        raise HTTPException(404, "Dazu liegt kein Ergebnis mehr vor.")
    return {k: v for k, v in eintrag.items() if k != "zeit"}


async def _chat_ausfuehren(anfrage: ChatAnfrage) -> dict:
    try:
        ergebnis = await _chat_antworten(anfrage)
    except HTTPException as exc:
        if anfrage.anfrage_id:
            _chat_laeufe[anfrage.anfrage_id] = {"zeit": time.time(), "zustand": "fehler", "detail": str(exc.detail)}
        raise
    except Exception:
        if anfrage.anfrage_id:
            _chat_laeufe[anfrage.anfrage_id] = {"zeit": time.time(), "zustand": "fehler", "detail": "Das ging schief."}
        raise
    if anfrage.anfrage_id:
        _chat_laeufe[anfrage.anfrage_id] = {"zeit": time.time(), "zustand": "fertig", "ergebnis": ergebnis}
    return ergebnis


def _strang_anhaengen(ids: list[int], strang, rolle: str, text: str, **felder) -> None:
    """Eine Zeile in den Chat-Strang; ein Fehler dort darf die Antwort nie verhindern."""
    try:
        ids.append(strang.anhaengen(rolle, text, **felder))
    except Exception:                                  # noqa: BLE001
        log.exception("Chat-Strang: Zeile nicht abgelegt")


async def _chat_antworten(anfrage: ChatAnfrage) -> dict:
    if not config.gemini_schluessel():
        raise HTTPException(503, "Kein Gemini-Schlüssel hinterlegt.")
    letzte = anfrage.nachrichten[-1]
    if letzte.rolle != "du":
        raise HTTPException(400, "Die letzte Nachricht muss von dir sein.")

    blatt = None
    if letzte.datei:                                   # erst prüfen und ablegen, dann erst Gemini fragen
        try:
            blatt = aufgabenspeicher.blatt_ablegen(letzte.datei.name, letzte.datei.data)
        except aufgabenspeicher.AufgabenFehler as fehler:
            raise HTTPException(400, str(fehler)) from None
    proto = speicher.standard()
    gespraech = anfrage.gespraech
    if not (gespraech and proto.gibt_es(gespraech, "chat")):
        gespraech = proto.gespraech_beginnen("chat")
    kopf = f"[Bild] {letzte.text}" if letzte.bild else (f"[PDF] {blatt['name']} {letzte.text}".strip() if blatt else letzte.text)
    proto.beitrag(gespraech, "du", kopf, "bild" if letzte.bild else "text")
    bild_id = None
    if letzte.bild:
        try:
            bild_id = bilder.speichern(gespraech, letzte.bild.mime, letzte.bild.data, letzte.text)
        except Exception:
            pass  # Beiwerk — das Gespräch geht vor

    strang = chatstrang.standard() if anfrage.strang else None
    strang_ids: list[int] = []
    if strang is not None:
        # Kontext aus dem Strang seit „Chat leeren“ (VOR der neuen Zeile gelesen); vom Browser zählt nur die letzte Nachricht.
        nachrichten = [{"rolle": z["rolle"], "text": z["text"], "bild": None, "datei": None} for z in strang.kontext()]
        nachrichten.append(letzte.model_dump())
        _strang_anhaengen(strang_ids, strang, "du", kopf.strip() or "[leer]", gespraech=gespraech)
    else:
        nachrichten = [n.model_dump() for n in anfrage.nachrichten]
    for n in nachrichten[:-1]:
        n["datei"] = None                              # nur die letzte Nachricht trägt ein PDF
    if blatt:
        nachrichten[-1]["datei"]["blatt"] = blatt["id"]
    try:
        ergebnis = await chat.antworten(nachrichten, gespraech=gespraech, kennung=anfrage.kennung)
    except chat.ChatFehler as fehler:
        raise HTTPException(502, str(fehler)) from None

    for l in ergebnis["links"]:
        proto.beitrag(gespraech, "system", f"Link {'gelesen' if l['ok'] else 'nicht gelesen'}: {l['url']}", "link")
    # Mit Ergebnis, wie bei der Sprachsitzung — sonst ist im Protokoll nicht
    # nachvollziehbar, worauf eine Antwort beruhte.
    for w in (ergebnis.get("werkzeug_ergebnisse")
              or [{"name": n, "text": ""} for n in ergebnis["werkzeuge"]]):
        text = f"Werkzeug {w['name']}: {w['text']}" if w["text"] else f"Werkzeug benutzt: {w['name']}"
        proto.beitrag(gespraech, "system", text, "werkzeug")
    if ergebnis.get("geprueft") not in (None, "ok"):   # der Wächter hat eine falsche Behauptung abgefangen (P3): nur ins Protokoll
        proto.beitrag(gespraech, "system", "Antwort geändert, weil sie etwas als erledigt nannte, was nicht passiert war: "
                      + " | ".join(ergebnis.get("pruefung") or [])[:300], "korrektur")
    proto.beitrag(gespraech, "friday", ergebnis["antwort"])
    if strang is not None:                             # Anzeige-Reihenfolge wie im Browser: Werkzeug-Hinweise, Antwort, Quittungen
        for name in ergebnis.get("werkzeuge") or []:
            _strang_anhaengen(strang_ids, strang, "friday", str(name), art="hinweis", gespraech=gespraech)
        _strang_anhaengen(strang_ids, strang, "friday", ergebnis["antwort"], gespraech=gespraech)
    for q in ergebnis.get("quittungen") or []:        # Fakt-Zeilen des Kerns (P2): im Protokoll wie im Live-Kanal, art „quittung“
        proto.beitrag(gespraech, "system", q["text"], "quittung")
        if strang is not None:
            _strang_anhaengen(strang_ids, strang, "friday", q["text"], art="quittung", status=q.get("status"), gespraech=gespraech)
    if bild_id:
        bilder.beschreiben(bild_id, ergebnis["antwort"])
    # Die Werkzeugausgaben bleiben im Protokoll; der Browser braucht sie nicht
    # und sie können lang werden (ganze Stundenpläne).
    return {**{k: v for k, v in ergebnis.items() if k != "werkzeug_ergebnisse"},
            "gespraech": gespraech, **({"strang_ids": strang_ids} if strang is not None else {})}


# ---------- Gedächtnis-Seite ----------
# Bestätigen und Verwerfen gibt es NUR hier, nie als Werkzeug fürs Modell:
# ein Vorschlag stammt aus fremdem Text, und wer ihn geschrieben hat, darf ihn
# nicht selbst durchwinken können (Entscheidung aus dem alten Friday).

class FaktEingabe(BaseModel):
    thema: str = Field(min_length=1, max_length=200)
    inhalt: str = Field(min_length=1, max_length=2000)


class ThemaEingabe(BaseModel):
    thema: str = Field(min_length=1, max_length=200)


class Entscheidung(BaseModel):
    entscheidung: Literal["ja", "nein"]


@app.get("/api/gedaechtnis")
def gedaechtnis_uebersicht() -> dict:
    g, p = gspeicher.standard(), speicher.standard()
    return {
        "fakten": [{"thema": f["thema"], "inhalt": f["inhalt"], "quelle": f["quelle"],
                    "seit": gspeicher.lesbar(f["gueltig_ab"])} for f in g.fakten()],
        "vorschlaege": [{"id": v["id"], "thema": v["thema"], "inhalt": v["inhalt"],
                         "herkunft": v["herkunft"] or v["quelle"], "zeit": gspeicher.lesbar(v["zeit"])}
                        for v in g.vorschlaege()],
        "gespraeche": [{"id": x["id"], "art": x["art"], "beginn": gspeicher.lesbar(x["beginn"]),
                        "zusammenfassung": x["zusammenfassung"], "beitraege": x["beitraege"],
                        "person": (personenregister.holen(x["person"]) or {}).get("name") if x["person"] else None}
                       for x in p.gespraeche(30) if x["beitraege"]],
        "graph": {"eingerichtet": bool(graph.url()), **p.graph_stand()},
    }


@app.get("/api/gedaechtnis/graph")
async def gedaechtnis_graph(frage: str) -> dict:
    from kern.werkzeuge.gedaechtnis import datum
    fakten = await graph.suchen(frage[:500], anzahl=10)
    return {"eingerichtet": bool(graph.url()),
            "fakten": [{"fakt": f["fact"], "seit": datum(f.get("valid_at")), "bis": datum(f.get("invalid_at"))}
                       for f in fakten]}


@app.get("/api/gedaechtnis/historie")
def gedaechtnis_historie(thema: str) -> list[dict]:
    return [{"inhalt": f["inhalt"], "quelle": f["quelle"], "von": gspeicher.lesbar(f["gueltig_ab"]),
             "bis": gspeicher.lesbar(f["gueltig_bis"]) if f["gueltig_bis"] else None}
            for f in gspeicher.standard().historie(thema)]


@app.post("/api/gedaechtnis/fakten")
def gedaechtnis_merken(e: FaktEingabe) -> dict:
    gspeicher.standard().merken(e.thema, e.inhalt, quelle="eike")
    return {"ok": True}


@app.get("/api/vorgaben")
def vorgaben_liste(antwort: Response) -> dict:
    antwort.headers["Cache-Control"] = "no-store"
    from kern.vorgaben import fach as vorgabenfach
    return {"aktive": vorgabenfach.standard().aktive()}


class VorgabeEingabe(BaseModel):
    # Länge und Inhalt prüft das Fach (dieselben Regeln wie das Werkzeug); hier nur eine grobe Obergrenze.
    text: str = Field("", max_length=2000)
    bereich: Optional[str] = Field(None, max_length=40)


def _vorgabe_ergebnis(e: dict) -> dict:
    """Das Fach meldet Absagen als {ok: False, grund} — für die Seite als 409 mit Klartext."""
    if not e.get("ok"):
        raise HTTPException(409, e.get("grund") or "Das ging nicht.")
    return e


@app.post("/api/vorgaben")
def vorgabe_anlegen_web(e: VorgabeEingabe) -> dict:
    """Formular im Notizbuch: dasselbe wie `vorgabe_merken` (Prüfung, Limit, Dubletten im Fach)."""
    from kern.vorgaben import fach as vorgabenfach
    return _vorgabe_ergebnis(vorgabenfach.standard().anlegen(e.text, e.bereich or "allgemein"))


@app.post("/api/vorgaben/{vorgabe_id}/aendern")
def vorgabe_aendern_web(vorgabe_id: int, e: VorgabeEingabe) -> dict:
    """Formular im Notizbuch: dasselbe wie `vorgabe_aendern` (die alte Vorgabe bleibt als ersetzt im Verlauf)."""
    from kern.vorgaben import fach as vorgabenfach
    return _vorgabe_ergebnis(vorgabenfach.standard().ersetzen(vorgabe_id, e.text, e.bereich or None))


@app.post("/api/vorgaben/{vorgabe_id}/widerrufen")
def vorgabe_widerrufen_web(vorgabe_id: int) -> dict:
    from kern.vorgaben import fach as vorgabenfach
    return vorgabenfach.standard().widerrufen(vorgabe_id)


@app.post("/api/gedaechtnis/verdichten")
async def gedaechtnis_verdichten() -> dict:
    from kern.gedaechtnis import verdichten
    gruppen = await verdichten.vorschlag()
    for g in gruppen:
        gspeicher.standard().zusammenfuehren(g["thema"], g["inhalt"], g["ersetzt"])
    return {"ok": True, "anzahl": len(gruppen),
            "zusammengefuehrt": [{"thema": g["thema"], "ersetzt": g["ersetzt"]} for g in gruppen]}


@app.post("/api/gedaechtnis/vergessen")
def gedaechtnis_vergessen(e: ThemaEingabe) -> dict:
    return {"ok": True, "anzahl": gspeicher.standard().vergessen(e.thema)}


@app.post("/api/gedaechtnis/vorschlaege/{vorschlag_id}")
def gedaechtnis_entscheiden(vorschlag_id: str, e: Entscheidung) -> dict:
    g = gspeicher.standard()
    ok = bool(g.bestaetigen(vorschlag_id)) if e.entscheidung == "ja" else g.verwerfen(vorschlag_id)
    if not ok:
        raise HTTPException(404, "Diesen offenen Vorschlag gibt es nicht (mehr).")
    return {"ok": True}


@app.get("/api/gedaechtnis/wissen")
async def wissen_uebersicht() -> dict:
    return await wissensgraph.uebersicht()


class FokusSchalter(BaseModel):
    an: bool


def _fokus_person(p: dict) -> dict:
    neu = sorted(p.get("neu") or [], key=lambda n: n["zeit"], reverse=True)
    seite = wissensseite.gemerkt(p["uuid"]) or {}               # die zuletzt gebaute Seite (ohne Modellaufruf): Beziehung als Untertitel
    namen = {c["id"]: c.get("name") or c["id"] for c in whatsappdienst.frei()}
    zuordnung = [{"chat_id": cid, "chat": namen.get(cid, cid), "absender": a}
                 for cid, liste in ((p.get("quellen", {}).get("whatsapp") or {}).get("zuordnung") or {}).items() for a in liste]
    return {"uuid": p["uuid"], "name": p["name"], "seit": p["seit"], "neu": neu, "untertitel": seite.get("untertitel", ""),
            "unklar": p.get("unklar", []), "zuordnung": zuordnung, "quellen": fokussammler.quellenstand(p)}


@app.get("/api/fokus")
def fokus_liste() -> dict:
    """Markierte Personen mit „Neu“ und Quellenstand (jüngste Neuigkeit zuerst)."""
    personen = [_fokus_person(p) for p in fokusablage.liste()]
    personen.sort(key=lambda p: (not p["neu"], "" if not p["neu"] else "", p["name"].lower()))
    personen.sort(key=lambda p: p["neu"][0]["zeit"] if p["neu"] else "", reverse=True)
    return {"personen": personen}


@app.put("/api/fokus/{uuid}")
async def fokus_schalten(uuid: str, s: FokusSchalter) -> dict:
    if not wissensgraph.UUID.match(uuid):
        raise HTTPException(422, "Ungültige Kennung.")
    if not s.an:                                       # Entfernen geht immer, auch wenn der Graph gerade nicht antwortet
        fokusablage.markieren(uuid, "", False)
        return {"an": False}
    zeilen = []
    if wissensgraph.eingerichtet():
        try:
            zeilen = await wissensgraph._abfrage("MATCH (n:Entity {uuid: $u}) RETURN n.name, labels(n)", {"u": uuid})
        except Exception:  # noqa: BLE001
            zeilen = []
    if not zeilen or wissensgraph._typ(zeilen[0][1]) != "Person":
        raise HTTPException(404, "Das ist keine Person im Gedächtnis.")
    return {"an": fokusablage.markieren(uuid, str(zeilen[0][0]), True)}


class FokusZuordnung(BaseModel):
    chat_id: str = Field(min_length=3, max_length=100)
    absender: str = Field(min_length=1, max_length=80)
    an: bool = True


@app.put("/api/fokus/{uuid}/zuordnung")
def fokus_zuordnung(uuid: str, e: FokusZuordnung) -> dict:
    """Eike ordnet einen Absender in einer freigegebenen Gruppe dieser Person zu (bei Namensgleichheit: „Kontakt F“ in der Gruppe ist Kontakt F Muster)."""
    if not wissensgraph.UUID.match(uuid):
        raise HTTPException(422, "Ungültige Kennung.")
    if not fokusablage.holen(uuid):
        raise HTTPException(404, "Diese Person ist nicht im Fokus.")
    if not (e.chat_id.endswith("@g.us") and whatsappdienst.ist_frei(e.chat_id)):
        raise HTTPException(422, "Das ist keine freigegebene Gruppe.")        # Default-Deny: nur Gruppen, die Eike freigegeben hat
    fokusablage.zuordnung_setzen(uuid, e.chat_id, e.absender, e.an)
    return {"an": e.an}


@app.post("/api/fokus/{uuid}/jetzt")
async def fokus_jetzt(uuid: str) -> dict:
    if not fokusablage.holen(uuid):
        raise HTTPException(404, "Diese Person ist nicht im Fokus.")
    if not fokussammler.jetzt_erlaubt(uuid):
        raise HTTPException(429, "Gerade erst nachgesehen — in ein paar Minuten wieder.")
    ergebnis = await fokussammler.sammle_person(uuid)
    return {**ergebnis, "person": _fokus_person(fokusablage.holen(uuid) or {"uuid": uuid, "name": "", "seit": ""})}


@app.get("/api/gedaechtnis/themen")
async def wissen_themen() -> dict:
    """Themen-Karten der Wissen-Seite, nach Wichtigkeit und Relevanz sortiert (`gedaechtnis/themen.py`)."""
    return await wissensthemen.themen()


@app.get("/api/gedaechtnis/treffer")
async def wissen_treffer(frage: str = Query(min_length=1, max_length=500)) -> dict:
    """Einträge, deren Name zur Frage passt — schnell (ohne Modell); `oeffnen` nennt den Eintrag, den die Seite gleich öffnen soll."""
    treffer, oeffnen = await wissensantwort._treffer(" ".join(frage.split()))
    return {"treffer": treffer, "oeffnen": oeffnen}


@app.get("/api/gedaechtnis/antwort")
async def wissen_antwort(frage: str = Query(min_length=1, max_length=500)) -> dict:
    """Kurze Antwort aus den Fakten des Graphen plus Belege (`gedaechtnis/antwort.py`)."""
    return await wissensantwort.antworten(frage)


@app.get("/api/gedaechtnis/seite/{uuid}")
async def wissen_seite(uuid: str, modell: int = Query(1, ge=0, le=1)) -> dict:
    """Detailseite zu einem Eintrag (`gedaechtnis/seite.py`). `modell=0`: sofort (gemerkte oder einfache Fassung, `veredelt`
    sagt welche); `modell=1`: wartet bei Bedarf auf das Modell."""
    s = await wissensseite.seite(uuid, modell=bool(modell))
    if not s:
        raise HTTPException(404, "Diesen Eintrag gibt es nicht.")
    return {**s, "notizen": wissensnotizen.fuer(uuid)}      # Eikes eigene Angaben mit Stand — ändern sich öfter als die gemerkte Seite


class NotizEingabe(BaseModel):
    art: str = Field(pattern="^(name|angabe|frei)$")
    wert: str = Field(default="", max_length=300)
    label: str = Field(default="", max_length=100)
    text: str = Field(default="", max_length=1000)


@app.post("/api/gedaechtnis/seite/{uuid}/notiz")
async def wissen_notiz(uuid: str, e: NotizEingabe) -> dict:
    """Eikes eigene Angabe zu einem Eintrag („Kontakt Bs Mutter heißt Kontakt H“): sofort ins Notizbuch (Quelle Eike, wirkt auf die
    Seite) und als vorgemerkte Graph-Episode mit Vorrang (`gedaechtnis/notizen.py`). Keine zweite Wahrheit neben dem Graphen."""
    if not wissensgraph.UUID.match(uuid):
        raise HTTPException(422, "Ungültige Kennung.")
    zeilen = []
    if wissensgraph.eingerichtet():
        try:
            zeilen = await wissensgraph._abfrage("MATCH (n:Entity {uuid: $u}) RETURN n.name", {"u": uuid})
        except Exception:  # noqa: BLE001
            zeilen = []
    if not zeilen:
        raise HTTPException(404, "Diesen Eintrag gibt es nicht.")
    try:
        notiz = wissensnotizen.anlegen(uuid, str(zeilen[0][0]), e.art, wert=e.wert, label=e.label, text=e.text)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True, "notiz": notiz}


@app.get("/api/gedaechtnis/wissen/{uuid}")
async def wissen_entitaet(uuid: str) -> dict:
    e = await wissensgraph.entitaet(uuid)
    if not e:
        raise HTTPException(404, "Diese Entität gibt es nicht.")
    return e


class BildName(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@app.get("/api/bilder")
def bilder_liste() -> list[dict]:
    return [{**b, "zeit": gspeicher.lesbar(b["zeit"])} for b in bilder.liste()]


@app.get("/api/netzbild/{kennung}")
def netzbild(kennung: str) -> Response:
    """Kopie eines Bildes aus dem Netz, das Friday gezeigt hat — Eikes Gerät fragt nie den fremden Server."""
    b = bildsuche.bild(kennung)
    if not b:
        raise HTTPException(404, "Dieses Bild gibt es nicht (mehr).")
    return Response(b[0], media_type=b[1], headers={"X-Content-Type-Options": "nosniff"})


@app.get("/api/bilder/{bild_id}")
def bild_datei(bild_id: str):
    pfad = bilder.pfad(bild_id)
    if not pfad:
        raise HTTPException(404, "Dieses Bild gibt es nicht.")
    return FileResponse(pfad, media_type=bilder.eintrag(bild_id)["mime"])


@app.delete("/api/bilder/{bild_id}")
def bild_entfernen(bild_id: str, request: Request) -> dict:
    """Ein Bild endgültig aus dem Gedächtnis entfernen — nur Eikes Tipp in der App (kein Werkzeug für das Modell)."""
    _gleiche_herkunft(request)
    if not bilder.entfernen(bild_id):
        raise HTTPException(404, "Dieses Bild gibt es nicht.")
    friday_karten.bild_entfernt(f"/api/bilder/{bild_id}")
    return {"ok": True}


@app.post("/api/bilder/{bild_id}/name")
async def bild_benennen(bild_id: str, e: BildName) -> dict:
    if not bilder.benennen(bild_id, e.name):
        raise HTTPException(404, "Dieses Bild gibt es nicht.")
    b = bilder.eintrag(bild_id)
    # Auch nachträglich benannte Bilder sollen am richtigen Namen im
    # Langzeitgedächtnis hängen — eine kleine eigene Episode.
    asyncio.create_task(graph.einspeisen(
        f"bild-{bild_id}-{e.name.strip().lower()}",
        f"Eike ordnet ein Bild zu: es zeigt {e.name.strip()}. Friday beschrieb es: {(b.get('beschreibung') or '')[:300]}",
        "Bild auf der Gedächtnis-Seite benannt", b["zeit"]))
    return {"ok": True}


@app.get("/api/gespraeche/{gespraech_id}")
def gespraech_ansehen(gespraech_id: int) -> list[dict]:
    return [{"zeit": gspeicher.lesbar(b["zeit"]), "rolle": b["rolle"], "art": b["art"], "text": b["text"]}
            for b in speicher.standard().beitraege(gespraech_id)]


# ---------- Nachdenken ----------

class NachdenkenSchalter(BaseModel):
    an: bool


_denk_aufgaben: set = set()


@app.get("/api/gedanken")
def gedanken_liste(antwort: Response) -> dict:
    antwort.headers["Cache-Control"] = "no-store"
    b = gedankenbuch.standard()
    return {"an": denkenlauf.ist_an(), "backend": denkrunde.backend(), "gedanken": b.neueste(60), "runden": b.runden(20),
            "faeden": b.faeden(limit=50)}


class FadenNeu(BaseModel):
    titel: str = Field(min_length=1, max_length=80)
    aufgabe: str = Field(min_length=1, max_length=400)


class FadenStatus(BaseModel):
    status: Literal["offen", "ruht", "erledigt"]


@app.post("/api/gedanken/faeden")
def gedanken_faden_neu(f: FadenNeu) -> dict:
    return gedankenbuch.standard().faden_anlegen(f.titel, f.aufgabe, quelle="eike")


@app.post("/api/gedanken/faeden/{faden_id}")
def gedanken_faden_status(faden_id: int, s: FadenStatus) -> dict:
    return gedankenbuch.standard().faden_status_setzen(faden_id, s.status)


class FrageAntwort(BaseModel):
    option: int | None = Field(default=None, ge=1, le=4)
    text: str | None = Field(default=None, max_length=300)


@app.get("/api/fragen")
def fragen_liste(antwort: Response) -> dict:
    antwort.headers["Cache-Control"] = "no-store"
    f = fragenfach.standard()
    return {"offen": f.offene(), "beantwortet": f.beantwortet_seit(48)}


@app.post("/api/fragen/{fid}/antwort")
def fragen_antworten(fid: int, a: FrageAntwort) -> dict:
    """Nur Eikes Tipp in der App (fremde Origins lehnt `frisch_halten` ab). Entweder eine Option oder ein eigener Text."""
    text = (a.text or "").strip()
    if (a.option is None) == (not text):
        raise HTTPException(422, "Entweder eine Option oder ein eigener Text.")
    f = fragenfach.standard()
    frage = f.eintrag(fid)
    if not frage:
        raise HTTPException(404, "Diese Frage kenne ich nicht.")
    if frage["status"] != "offen":
        raise HTTPException(409, "Diese Frage ist schon beantwortet oder abgelaufen.")
    r = f.beantworten(fid, option=a.option, text=text)
    if not r["ok"]:
        raise HTTPException(409 if "schon beantwortet" in r["grund"] else 422, r["grund"])
    return r


@app.post("/api/gedanken/einstellung")
def gedanken_einstellen(s: NachdenkenSchalter) -> dict:
    denkenlauf.einstellen(s.an)
    return {"an": denkenlauf.ist_an()}


class ChatBackend(BaseModel):
    backend: Literal["gemini", "deepseek"]


@app.get("/api/chat/backend")
def chat_backend(antwort: Response) -> dict:
    antwort.headers["Cache-Control"] = "no-store"
    return {"backend": chatmodell.backend()}


@app.post("/api/chat/backend")
def chat_backend_setzen(s: ChatBackend) -> dict:
    """Nur Eikes Knopf in der App (fremde Origins lehnt `frisch_halten` ab). Kein Werkzeug schreibt diese Einstellung."""
    return {"backend": chatmodell.einstellen(s.backend)}


class DenkBackend(BaseModel):
    backend: Literal["gemini", "opencode", "deepseek"]


@app.get("/api/gedanken/backend")
def gedanken_backend(antwort: Response) -> dict:
    antwort.headers["Cache-Control"] = "no-store"
    return {"backend": denkrunde.backend()}


@app.post("/api/gedanken/backend")
def gedanken_backend_setzen(s: DenkBackend) -> dict:
    """Nur Eikes Knopf in der App (fremde Origins lehnt `frisch_halten` ab). Kein Werkzeug schreibt diese Einstellung."""
    return {"backend": denkrunde.backend_einstellen(s.backend)}


@app.post("/api/gedanken/jetzt")
async def gedanken_jetzt() -> dict:
    """Eine Denkrunde sofort, im Hintergrund (Knopf im Reiter Gedanken). Höchstens eine zur selben Zeit."""
    if denkenlauf._laeuft:
        return {"gestartet": False, "grund": "Eine Denkrunde läuft gerade."}
    t = asyncio.create_task(denkenlauf.jetzt_nachdenken())
    _denk_aufgaben.add(t)
    t.add_done_callback(_denk_aufgaben.discard)
    return {"gestartet": True, "grund": ""}


# ---------- Freigaben: Konnektoren ----------
# Das Passwort kommt nur herein, nie heraus. Die Übersicht nennt höchstens
# den Benutzernamen.

class Schalter(BaseModel):
    aktiv: bool


class Zugang(BaseModel):
    benutzer: str = Field(min_length=1, max_length=200)
    passwort: str = Field(min_length=1, max_length=500)


class BotModell(BaseModel):
    modell: str = Field(min_length=1, max_length=20)


def _konnektor(kid: str) -> None:
    if not konnektoren.gibt_es(kid):
        raise HTTPException(404, "Diesen Konnektor gibt es nicht.")


@app.get("/api/konnektoren")
def konnektoren_liste() -> list[dict]:
    liste = konnektoren.uebersicht()
    for k in liste:
        if k["id"] == "schulmanager" and (s := ablage.standard().stand()):
            teile = [f"{s.get('verarbeitet', 0)} Elternbriefe verarbeitet"]
            if s.get("neu"):
                teile.append(f"{s['neu']} warten")
            if s.get("fehler"):
                teile.append(f"{s['fehler']} mit Fehler")
            k["stand"] = " · ".join(teile)
        if k["id"] == "spotify":
            # Für Spotify zählt nicht nur der Zugang, sondern die erteilte Erlaubnis.
            k["verbunden"] = spotify.verbunden()
            k["rueckadresse"] = spotify.rueckadresse()
            k["stand"] = "verbunden" if k["verbunden"] else "noch nicht verbunden"
        if k["id"] == "studio":
            # Dasselbe für den YouTube-Kanal: Ohne diese Felder zeigt die Seite
            # weder die Rückadresse noch einen Verbinden-Knopf.
            k["verbunden"] = studio.verbunden()
            k["rueckadresse"] = studio.rueckadresse()
            k["stand"] = (f"mit {studio.KANAL} verbunden" if k["verbunden"]
                          else "noch nicht verbunden")
        if k["id"] == "iserv_mail" and (s := postablage.standard().stand()):
            teile = [f"{sum(s.values())} Mails gespeichert", f"{s.get('verarbeitet', 0)} vorbereitet"]
            if s.get("neu"):
                teile.append(f"{s['neu']} warten")
            if s.get("fehler"):
                teile.append(f"{s['fehler']} mit Fehler")
            k["stand"] = " · ".join(teile)
        if k["id"] == "minecraft_bot":
            # Welches Modell der Bot gerade benutzt, weiß nur der Helfer auf hphome (er liest das Profil).
            k["modell"] = minecraftbotdienst.botmodell()
    return liste


@app.post("/api/konnektoren/{kid}/abgleichen")
async def konnektor_abgleichen(kid: str) -> dict:
    _konnektor(kid)
    if not konnektoren.benutzbar(kid):
        raise HTTPException(409, "Erst einschalten und Zugangsdaten eintragen.")
    if kid == "iserv_mail":
        asyncio.create_task(abgleich.durchgang(pause=abgleich.PAUSE_S))
        was = "neue Mails"
    elif kid == "goodnotes":
        goodnotesabgleich.starten()
        was = "neue und geänderte Hefte"
    else:
        asyncio.create_task(briefe.durchgang(briefe.PRO_DURCHGANG, pause=briefe.PAUSE_S))
        was = "neue Elternbriefe"
    return {"ok": True, "text": f"Friday holt {was} und bereitet sie im Hintergrund vor. "
                                "Schau in ein paar Minuten wieder rein."}


@app.post("/api/konnektoren/{kid}")
def konnektor_schalten(kid: str, s: Schalter) -> dict:
    _konnektor(kid)
    konnektoren.setzen(kid, s.aktiv)
    return {"ok": True}


@app.post("/api/konnektoren/{kid}/schreiben")
def konnektor_schreiben(kid: str, s: Schalter) -> dict:
    """Schreiben ist eine eigene Freigabe — Lesen bleibt davon unberührt."""
    _konnektor(kid)
    if not konnektoren.KONNEKTOREN[kid].get("schreibbar"):
        raise HTTPException(409, "Dieser Konnektor kann nichts schreiben.")
    konnektoren.schreiben_setzen(kid, s.aktiv)
    return {"ok": True}


@app.post("/api/konnektoren/{kid}/modell")
async def konnektor_botmodell(kid: str, w: BotModell) -> dict:
    """Das Modell des Minecraft-Bots umstellen (Gemini oder DeepSeek). Nur Eikes Tipp in der App, kein Werkzeug
    fürs Modell; der Helfer auf hphome schreibt Profil und Schlüssel und startet den Bot neu."""
    _konnektor(kid)
    if kid != "minecraft_bot":
        raise HTTPException(409, "Nur der Minecraft-Bot hat ein Modell.")
    if not konnektoren.aktiv(kid):
        raise HTTPException(409, "Erst den Minecraft-Bot einschalten.")
    text = await minecraftbotdienst.botmodell_setzen(w.modell)
    ok = not text.startswith("NICHT ERLEDIGT")
    return {"ok": ok, "text": text, "modell": minecraftbotdienst.botmodell()}


@app.get("/api/snapchat/wachzeiten")
def snapchat_wachzeiten() -> dict:
    """Ob der Helfer rund um die Uhr arbeitet — nur Eikes Tipp in der App, kein Werkzeug fürs Modell."""
    return {"rund_um_die_uhr": snapchatdienst.wachzeiten_rund_um_die_uhr()}


@app.post("/api/snapchat/wachzeiten")
def snapchat_wachzeiten_setzen(s: Schalter, request: Request) -> dict:
    _gleiche_herkunft(request)
    return {"ok": True, "rund_um_die_uhr": snapchatdienst.wachzeiten_schreiben(s.aktiv)}


@app.post("/api/konnektoren/{kid}/zugang")
def konnektor_zugang(kid: str, z: Zugang) -> dict:
    _konnektor(kid)
    if konnektoren.KONNEKTOREN[kid].get("zugang_von"):
        raise HTTPException(409, "Dieser Konnektor nutzt die Anmeldung eines anderen.")
    # Sonst landen Passwörter bei Diensten, die sie nie brauchen (GVH, 13.09.2026).
    if konnektoren.KONNEKTOREN[kid].get("ohne_zugang"):
        raise HTTPException(409, "Dieser Dienst braucht keine Anmeldung.")
    konnektoren.zugang_setzen(kid, z.benutzer, z.passwort)
    schulmanager.vergessen()   # neue Daten: alte Sitzung und Sperre weg
    iserv_mail.vergessen()
    icloud.vergessen()
    gmail.vergessen()
    webdav.vergessen()
    return {"ok": True}


@app.delete("/api/konnektoren/{kid}/zugang")
def konnektor_zugang_loeschen(kid: str) -> dict:
    _konnektor(kid)
    if konnektoren.KONNEKTOREN[kid].get("zugang_von"):
        raise HTTPException(409, "Dieser Konnektor nutzt die Anmeldung eines anderen.")
    konnektoren.zugang_loeschen(kid)
    schulmanager.vergessen()
    iserv_mail.vergessen()
    icloud.vergessen()
    gmail.vergessen()
    webdav.vergessen()
    return {"ok": True}


@app.post("/api/konnektoren/{kid}/pruefen")
async def konnektor_pruefen(kid: str) -> dict:
    _konnektor(kid)
    try:
        if kid == "iserv_mail":
            ok, text = True, await asyncio.to_thread(iserv_mail.pruefen)
        elif kid == "icloud_kalender":
            ok, text = True, await asyncio.to_thread(icloud.pruefen)
        elif kid == "spotify":
            ok, text = True, await spotify.pruefen()
        elif kid == "studio":
            k = await studio.kanal()
            ok, text = True, (f"Verbunden mit dem Kanal {k['name']}: {k['videos']} Videos, "
                              f"{k['abonnenten']} Abonnenten.")
        elif kid == "gmail":
            ok, text = True, await asyncio.to_thread(gmail.pruefen)
        elif kid == "goodnotes":
            ok, text = True, await webdav.pruefen(goodnotesabgleich.wurzel())
        elif kid == "pc":
            st = await pcdienst.status()
            ok, text = True, (f"Verbunden mit {st.get('rechner')} ({st.get('benutzer')}), Helfer {st.get('version')}. "
                              f"Ordner: {', '.join(st.get('ordner') or []) or 'keine'}.")
        elif kid == "homeassistant":
            ok, text = await hadienst.pruefen()
        elif kid == "schulmanager":
            s = await schulmanager.anmeldung()
            name = (s["benutzer"].get("firstname") or "").strip()
            ok, text = True, f"Angemeldet{' als ' + name if name else ''}. Der Schulmanager antwortet."
        else:
            # Kein Auffangfall mehr: Bis 14.09.2026 meldete „Anmeldung prüfen"
            # bei JEDEM anderen Dienst „Angemeldet als Eike. Der Schulmanager
            # antwortet." — und vermerkte das auch noch als dessen Prüfergebnis.
            ok, text = False, f"Für {kid} gibt es keine Anmeldeprüfung."
    except (schulmanager.AnmeldeFehler, iserv_mail.AnmeldeFehler, icloud.AnmeldeFehler,
            spotify.SpotifyFehler, studio.StudioFehler, gmail.AnmeldeFehler, webdav.AnmeldeFehler,
            pcdienst.PcFehler, hadienst.HomeAssistantFehler) as exc:
        ok, text = False, str(exc)
    except Exception as exc:
        ok, text = False, f"Der Dienst ist nicht erreichbar ({type(exc).__name__})."
    konnektoren.vermerken(kid, ok, text)
    return {"ok": ok, "text": text}


@app.get("/api/konnektoren/{kid}/vorschau")
async def konnektor_vorschau(kid: str) -> dict:
    """Was Friday sieht, wenn sie nachschaut — zum Ausprobieren auf der Seite."""
    _konnektor(kid)
    # Dienste ohne Anmeldung (GVH) haben nichts einzutragen — sonst hinge die
    # Vorschau dort für immer an dieser Sperre.
    if not konnektoren.KONNEKTOREN[kid].get("ohne_zugang") and not konnektoren.zugang(kid):
        raise HTTPException(409, "Erst Zugangsdaten eintragen.")
    if kid == "gvh":
        return {"text": await fahrplan.abfahrten("Hannover Hauptbahnhof")}
    if kid == "iserv_mail":
        return {"text": await post.mails(anzahl=5)}
    if kid == "icloud_kalender":
        return {"text": await kalenderwerkzeug.kalender("diese Woche")}
    if kid == "spotify":
        return {"text": await musikwerkzeug.musik()}
    if kid == "studio":
        return {"text": await studiowerkzeug.studio_stand()}
    if kid == "wetter":
        return {"text": await wetterwerkzeug.wetter("", "heute")}
    if kid == "gmail":
        eingang = await gmailwerkzeug.gmail_mails(anzahl=5)
        mails = await asyncio.to_thread(garantiepost._holen, "")
        faelle = [v for v in (garantiepost.auswerten(m) for m in mails) if v]
        return {"text": f"{eingang}\n\n{len(mails)} Mails der ÜSTRA, {len({v['vorgang'] for v in faelle})} Garantiefälle. "
                        + " · ".join(f"{v['vorgang']}: {garantiepost.TEXTE.get(v['art'], v['art'])}"
                                     for v in faelle[:5])}
    if kid == "goodnotes":
        st = goodnotesablage.standard().stand()
        text = f"{st['fertig']} von {st['gesamt']} Heftseiten abgeschrieben."
        treffer = goodnotesablage.standard().suchen("Aufgabe", anzahl=3)
        if treffer:
            text += " Beispiel-Treffer für „Aufgabe“: " + "; ".join(
                f"{t['name']} S. {t['nr']}" for t in treffer)
        return {"text": text}
    if kid == "pc":
        return {"text": await pcwerkzeug.pc_status()}
    if kid == "homeassistant":
        try:
            p = await hadienst.position()
        except hadienst.HomeAssistantFehler as exc:
            return {"text": str(exc)}
        if not p:
            return {"text": f"{hadienst.entitaet()} liefert gerade keine Position (unbekannt, ungenau oder ohne "
                            "Koordinaten)."}
        genau = f"Genauigkeit rund {p['genau']} m" if p["genau"] is not None else "Genauigkeit unbekannt"
        return {"text": f"Home Assistant liefert eine Position: vor {p['alter_s']} Sekunden gemeldet, {genau}. "
                        "Koordinaten zeigt die Vorschau nicht."}
    if kid == "fahrgastrechte":
        st = fahrgastrechte.stand(None)
        if not st["aktiv"]:
            # Aus: `stand` liefert dann nur die Regeln (gefunden am 22.09.2026 beim Live-Check — vorher KeyError, 500).
            return {"text": "Die Freigabe „Bahn-Fahrgastrechte“ ist aus — Friday sammelt keine Bahn-Fälle."}
        bereit = [e for e in st["entwuerfe"] if e["stand"] == "bereit"]
        return {"text": f"{len(st['faelle'])} Bahn-Fälle gesammelt, {len(bereit)} Anträge bereit"
                        + (f" ({fahrgastrechte.euro(sum(e['betrag_cent'] for e in bereit))})" if bereit else "")
                        + f", {st['summen']['genehmigt']} genehmigt ({fahrgastrechte.euro(st['summen']['genehmigt_cent'])}). "
                        f"{len(st['zuege'])} verspätete Züge ab 20 Minuten aus den letzten zwei Tagen gemerkt."}
    if kid == "hausaufgaben":
        liste = buecherablage.standard().alle()
        return {"text": "\n".join(f"{b['titel']} ({b['fach']}): {buecherablage.uebersicht(b)['buchseiten']} Buchseiten, "
                                   f"Stand {b['status']}" for b in liste) or "Noch kein Buch hochgeladen (Extras → HA)."}
    if kid == "schulmanager":
        plan = await schule.stundenplan("heute")
        aufgaben = await schule.hausaufgaben(7)
        return {"text": f"{plan}\n\nHausaufgaben der letzten 7 Tage:\n{aufgaben}"}
    # KEIN Auffangfall auf den Schulmanager. Bis 14.09.2026 landete hier jeder
    # Dienst ohne eigenen Zweig — „Wie läuft der Kanal?" zeigte Eike den
    # Stundenplan. Fremde Daten unter falscher Überschrift sind schlimmer als
    # ein fehlender Knopf.
    raise HTTPException(501, f"Für {kid} gibt es noch keine Vorschau.")


# ---------- Snapchat-Memories ----------
# Export-ZIPs in Teilen hochladen, Auftrag anlegen, Ergebnis holen. Gerechnet wird im Container `snapmem`
# (Auftragsdateien in data/snapmem/); Friday selbst rechnet nichts. Siehe kern/snapmem/auftraege.py.

def _snapmem(fn, *args):
    try:
        return fn(*args)
    except snapmemdienst.SnapmemFehler as exc:
        raise HTTPException(400, str(exc)) from None


class SnapmemStart(BaseModel):
    name: str = Field(max_length=200)
    groesse: int


class SnapmemAuftrag(BaseModel):
    zips: list[str] = Field(max_length=50)
    modus: str


@app.get("/api/snapmem")
def snapmem_stand() -> dict:
    return {"uploads": snapmemdienst.uploads(), "laeufe": snapmemdienst.laeufe(), "modi": list(snapmemdienst.MODI),
            "teil": snapmemdienst.TEIL}


@app.post("/api/snapmem/hochladen")
async def snapmem_start(daten: SnapmemStart) -> dict:
    await asyncio.to_thread(snapmemdienst.aufraeumen)
    return await asyncio.to_thread(_snapmem, snapmemdienst.upload_start, daten.name, daten.groesse)


@app.put("/api/snapmem/hochladen/{uid}/{nr}")
async def snapmem_teil(uid: str, nr: int, request: Request) -> dict:
    puffer = bytearray()
    async for stueck in request.stream():
        puffer += stueck
        if len(puffer) > snapmemdienst.TEIL:
            raise HTTPException(413, "Das Teil ist zu groß.")
    return await asyncio.to_thread(_snapmem, snapmemdienst.upload_teil, uid, nr, bytes(puffer))


@app.post("/api/snapmem/hochladen/{uid}/fertig")
async def snapmem_fertig(uid: str) -> dict:
    return await asyncio.to_thread(_snapmem, snapmemdienst.upload_fertig, uid)


@app.post("/api/snapmem/upload/{uid}/loeschen")
def snapmem_upload_loeschen(uid: str) -> dict:
    _snapmem(snapmemdienst.upload_loeschen, uid)
    return {"ok": True}


@app.post("/api/snapmem/auftrag")
def snapmem_auftrag(daten: SnapmemAuftrag) -> dict:
    return {"id": _snapmem(snapmemdienst.auftrag_anlegen, daten.zips, daten.modus)}


@app.get("/api/snapmem/auftrag/{aid}")
def snapmem_auftrag_stand(aid: str) -> dict:
    return _snapmem(snapmemdienst.status, aid)


@app.get("/api/snapmem/auftrag/{aid}/ergebnis")
def snapmem_ergebnis(aid: str) -> FileResponse:
    pfad = _snapmem(snapmemdienst.ergebnis_pfad, aid)
    if pfad is None:
        raise HTTPException(404, "Das Ergebnis ist noch nicht fertig oder schon gelöscht.")
    modus = _snapmem(snapmemdienst.status, aid).get("modus", "ergebnis")
    return FileResponse(pfad, media_type="application/zip", filename=f"snapchat-memories-{modus}.zip")


@app.post("/api/snapmem/auftrag/{aid}/loeschen")
def snapmem_auftrag_loeschen(aid: str) -> dict:
    _snapmem(snapmemdienst.loeschen, aid)
    return {"ok": True}


# ---------- WhatsApp-Verläufe ----------
# Hochladen, Personen zuordnen, auswerten. Rohe Chats bleiben auf dem Server;
# nur die Auswertung (Profile) geht — als Vorschläge — ins Gedächtnis.

@app.post("/api/chats/hochladen")
async def chats_hochladen(datei: UploadFile) -> dict:
    name = datei.filename or "chat.txt"
    if not name.lower().endswith((".txt", ".zip")):
        raise HTTPException(400, "Bitte eine .txt oder .zip aus dem WhatsApp-Export.")
    sicher = "".join(c for c in name if c.isalnum() or c in "._- ")[-80:] or "chat.txt"
    ziel = chatablage.standard().roh_ordner() / f"{os.urandom(8).hex()}-{sicher}"
    groesse = 0
    with open(ziel, "wb") as f:
        while stueck := await datei.read(1024 * 1024):
            f.write(stueck)
            groesse += len(stueck)
            if groesse > chat_einlesen.MAX_UPLOAD:
                f.close()
                ziel.unlink(missing_ok=True)
                raise HTTPException(413, "Die Datei ist größer als 500 MB.")
    try:
        chats = await asyncio.to_thread(chat_einlesen.einlesen, ziel, name)
    except chat_einlesen.ZuGross as exc:
        raise HTTPException(413, str(exc)) from None
    except Exception as exc:
        raise HTTPException(400, f"Die Datei ließ sich nicht einlesen ({type(exc).__name__}).") from None
    # Absender gleich selbst zuordnen — Eike soll das nicht für jede Person tun müssen.
    zuordnung = await asyncio.to_thread(chatablage.standard().zuordnen_automatisch)
    # Erst jetzt möglich: der Chatname ergibt sich aus den Absendern, die nicht Eike sind.
    zuordnung["benannt"] = await asyncio.to_thread(chatablage.standard().namen_nachtragen)
    return {"ok": True, "chats": [{"partner": c["partner"], "anzahl": c["anzahl"]} for c in chats],
            "nachrichten": sum(c["anzahl"] for c in chats), "zuordnung": zuordnung}


@app.post("/api/chats/zuordnen/automatisch")
async def chats_automatisch_zuordnen() -> dict:
    a = chatablage.standard()
    ergebnis = await asyncio.to_thread(a.zuordnen_automatisch)
    ergebnis["benannt"] = await asyncio.to_thread(a.namen_nachtragen)
    return {"ok": True, **ergebnis}


def _auswertung_stand(a) -> dict:
    """Läuft die Auswertung? Aus dem Serverzustand abgeleitet, damit der Hinweis
    ein Neuöffnen der App übersteht."""
    s = a.stand()
    ab = s["abschnitte"]
    offen_ab = ab.get("neu", 0) + ab.get("fehler", 0)
    fertig, gesamt = ab.get("fertig", 0), sum(ab.values())
    laeuft = chat_auswertung.offen() and not s["nicht_zugeordnet"]
    nachts = not (6 <= config.jetzt().hour < 23)
    if s["nicht_zugeordnet"]:
        text = "Warte: erst alle Absender einer Person zuordnen."
    elif offen_ab:
        text = f"Friday wertet die Verläufe aus — {fertig} von {gesamt} Abschnitten fertig."
        text += " Pausiert bis 6 Uhr." if nachts else " Läuft im Hintergrund, auch wenn du die App schließt."
    elif laeuft:
        text = "Friday baut die Personenprofile."
    elif gesamt:
        text = f"Auswertung abgeschlossen: {s['profile']} von {s['personen']} Personen ausgewertet."
    else:
        text = ""
    return {"laeuft": laeuft, "fertig": bool(gesamt) and not laeuft, "nachts": nachts, "text": text}


@app.get("/api/chats")
def chats_liste() -> dict:
    a = chatablage.standard()
    return {"stand": a.stand(), "auswertung": _auswertung_stand(a),
            "chats": [{"id": c["id"], "partner": c["partner"], "anzahl": c["anzahl"],
                       "von": c["von"][:10], "bis": c["bis"][:10], "auswerten": bool(c["auswerten"]),
                       "gruppe": bool(c["gruppe"])}
                      for c in a.chats()],
            "personen": [{"id": p["id"], "name": p["name"], "rolle": p["rolle"], "ich": bool(p["ich"]),
                          "aliase": p["aliase"], "profil": bool(p["profil"])} for p in a.personen()],
            "offen": a.offene_absender()}


class Person(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rolle: str = Field("", max_length=60)
    ich: bool = False


@app.post("/api/chats/personen")
def chats_person_anlegen(p: Person) -> dict:
    return {"ok": True, "id": chatablage.standard().person_anlegen(p.name, p.rolle, p.ich)}


class Zuordnung(BaseModel):
    absender: str = Field(min_length=1, max_length=120)
    person: int


@app.post("/api/chats/zuordnen")
def chats_zuordnen(z: Zuordnung) -> dict:
    chatablage.standard().zuordnen(z.absender, z.person)
    return {"ok": True}


class ChatSchalter(BaseModel):
    auswerten: bool


@app.post("/api/chats/{cid}/auswerten")
def chats_auswerten_schalten(cid: int, s: ChatSchalter) -> dict:
    a = chatablage.standard()
    if not a.chat(cid):
        raise HTTPException(404, "Diesen Verlauf gibt es nicht.")
    with a._sperre, a._db:
        a._db.execute("UPDATE chats SET auswerten = ? WHERE id = ?", (int(s.auswerten), cid))
    return {"ok": True}


@app.post("/api/chats/auswerten")
async def chats_auswerten_anstossen() -> dict:
    a = chatablage.standard()
    if a.stand()["nicht_zugeordnet"]:
        raise HTTPException(409, "Erst alle Absender einer Person zuordnen (oder als 'ich' markieren).")
    asyncio.create_task(chat_auswertung.durchgang(pause=chat_auswertung.PAUSE_S))
    return {"ok": True, "text": "Friday wertet die Verläufe im Hintergrund aus. Das dauert bei viel Text "
                                "eine Weile — der Fortschritt steht hier."}


class UmgebungMeldung(BaseModel):
    kennung: str = Field("", max_length=64)
    art: str = Field("", max_length=20)
    geraet: str = Field("", max_length=20)
    system: str = Field("", max_length=20)
    browser: str = Field("", max_length=20)
    breite: int = 0
    hoehe: int = 0


@app.post("/api/umgebung")
def umgebung_melden(meldung: UmgebungMeldung) -> dict:
    """Der Browser sagt, worin er läuft. Übernommen wird nur, was in den festen
    Listen steht (siehe kern/umgebung.py): Der Browser ist eine fremde Quelle,
    und das Ergebnis landet im System-Prompt."""
    return umgebung.melden(meldung.model_dump())


@app.get("/api/umgebung")
def umgebung_liste() -> dict:
    """Worin Friday gerade offen ist — für die Einstellungen-Seite.
    Nur wirklich offene Sitzungen, jede Oberfläche einmal."""
    return {"sitzungen": [dict(e, text=umgebung.satz(e)) for e in umgebung.offene()]}


class StandortMeldung(BaseModel):
    kennung: str = Field("", max_length=64)
    breite: float | None = None
    laenge: float | None = None
    genauigkeit: float | None = None
    fehler: str = Field("", max_length=30)


@app.get("/api/standort/warten")
async def standort_warten(seit: int = -1) -> dict:
    """Long-Poll der App: kehrt zurück, sobald Friday den Standort braucht.

    Die App holt die Position NUR, wenn `offen` gesetzt ist — also nicht beim Öffnen
    und nicht im Takt. Ist der Konnektor aus, antwortet der Server mit 403, und die
    App fragt den Browser nie (kein Erlaubnis-Dialog auf Vorrat)."""
    if not konnektoren.benutzbar("standort"):
        raise HTTPException(403, "Standort ist auf der Freigaben-Seite nicht eingeschaltet.")
    return await standortdienst.auf_anfrage_warten(seit)


@app.post("/api/standort")
def standort_melden(meldung: StandortMeldung) -> dict:
    """Die App meldet, wo sie ist. Default-Deny auch hier: Ist der Konnektor aus,
    wird nichts angenommen — nicht erst beim Auslesen ignoriert. Was der Browser
    schickt, wird geprüft und begrenzt (siehe kern/dienste/standort.py)."""
    if not konnektoren.benutzbar("standort"):
        raise HTTPException(403, "Standort ist auf der Freigaben-Seite nicht eingeschaltet.")
    try:
        return standortdienst.melden(meldung.model_dump())
    except ValueError:
        raise HTTPException(422, "Die Koordinaten sind ungültig.") from None


# ---------- WhatsApp ----------
# Default-Deny wie überall: Ist der Konnektor aus, geht keine dieser Routen.
# Welche CHATS freigegeben sind, prüft der Dienst selbst (kern/dienste/whatsapp.py).

class WhatsAppChats(BaseModel):
    chats: list[dict] = Field(default_factory=list)


def _whatsapp_frei() -> None:
    if not konnektoren.benutzbar("whatsapp"):
        raise HTTPException(403, "WhatsApp ist auf der Freigaben-Seite nicht eingeschaltet.")


@app.get("/api/whatsapp/status")
async def whatsapp_status() -> dict:
    """Verbunden, wartet auf QR-Code oder gar nicht gestartet?"""
    _whatsapp_frei()
    try:
        return await whatsappdienst.status()
    except whatsappdienst.WhatsAppFehler as exc:
        return {"zustand": "FEHLER", "text": str(exc), "nummer": ""}


@app.post("/api/whatsapp/start")
async def whatsapp_starten() -> dict:
    _whatsapp_frei()
    try:
        return await whatsappdienst.starten()
    except whatsappdienst.WhatsAppFehler as exc:
        raise HTTPException(502, str(exc)) from None


@app.post("/api/whatsapp/trennen")
async def whatsapp_trennen() -> dict:
    _whatsapp_frei()
    try:
        await whatsappdienst.trennen()
    except whatsappdienst.WhatsAppFehler as exc:
        raise HTTPException(502, str(exc)) from None
    return {"ok": True}


@app.get("/api/whatsapp/qr")
async def whatsapp_qr() -> Response:
    """Der QR-Code zum Koppeln. Verlässt den Server nur als Bild."""
    _whatsapp_frei()
    try:
        return Response(await whatsappdienst.qr_bild(), media_type="image/png",
                        headers={"Cache-Control": "no-store"})
    except whatsappdienst.WhatsAppFehler as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/api/whatsapp/chats")
async def whatsapp_chats_liste() -> dict:
    """ALLE Chats — nur für die Freigaben-Seite, damit Eike auswählen kann.
    Das Modell bekommt diese Liste nie zu sehen, nur die freigegebenen."""
    _whatsapp_frei()
    try:
        return {"chats": await whatsappdienst.alle_chats()}
    except whatsappdienst.WhatsAppFehler as exc:
        raise HTTPException(502, str(exc)) from None


@app.post("/api/whatsapp/chats")
def whatsapp_chats_setzen(wahl: WhatsAppChats) -> dict:
    """Welche Chats Friday lesen darf. Ohne Häkchen: keiner."""
    _whatsapp_frei()
    whatsappdienst.frei_setzen(wahl.chats)
    return {"ok": True, "anzahl": len(whatsappdienst.frei())}


# ---------- PC (Windows) ----------
# Rückfragen für heikle Aktionen am PC: Die App zeigt sie als Karte, Eikes Tipp
# kommt hier an — der EINZIGE Weg, auf dem Friday einer Aktion `bestaetigt`
# mitgibt (kern/dienste/pc.py). Kein Werkzeug führt hierher.

class PcEntscheidung(BaseModel):
    ja: bool


class PcEinrichten(BaseModel):
    adresse: str = Field("", max_length=200)
    neu: bool = False


class PcSchnell(BaseModel):
    aktion: str = Field(max_length=40)


# Nur was ohnehin ohne Rückfrage liefe — die Knöpfe in Extras → PC.
PC_SCHNELL = {"sperren", "bildschirm_aus", "lauter", "leiser", "stumm", "play_pause", "naechster", "vorheriger",
              "abbrechen"}


def _pc_frei(schreiben: bool = False) -> None:
    if not konnektoren.benutzbar("pc"):
        raise HTTPException(403, "Der PC ist auf der Freigaben-Seite nicht eingeschaltet oder nicht eingerichtet.")
    if schreiben and not konnektoren.schreiben_erlaubt("pc"):
        raise HTTPException(403, "„Am PC handeln erlauben“ ist aus.")


def _gleiche_herkunft(request: Request) -> None:
    """Eine fremde Webseite in Eikes Browser soll hier nichts auslösen können."""
    if not live.origin_erlaubt(request.headers.get("origin")):
        raise HTTPException(403, "Fremde Herkunft.")


@app.get("/api/pc/offen")
def pc_offen() -> dict:
    """Wartende Rückfragen und der Verlauf — die App fragt das alle paar Sekunden."""
    if not konnektoren.aktiv("pc"):
        return {"aktiv": False, "offen": [], "verlauf": []}
    return {"aktiv": True, "schreiben": konnektoren.schreiben_erlaubt("pc"), "offen": pcdienst.offene(),
            "verlauf": pcdienst.verlauf(15)}


@app.post("/api/pc/offen/{rid}")
async def pc_entscheiden(rid: str, e: PcEntscheidung, request: Request) -> dict:
    _gleiche_herkunft(request)
    return await pcdienst.entscheiden(rid[:64], e.ja)


@app.get("/api/pc/status")
async def pc_status() -> dict:
    _pc_frei()
    try:
        status = await pcdienst.status()
        neu = pcdienst.helfer_version() if pcdienst.helfer_neuer(status.get("version"), pcdienst.helfer_version()) else ""
        return {"ok": True, **status, "helfer_neu": neu}
    except pcdienst.PcFehler as exc:
        return {"ok": False, "text": str(exc)}


@app.get("/api/pc/bildschirm")
async def pc_bildschirm() -> Response:
    """Nur für Eikes Augen in der App — geht nicht an Gemini."""
    _pc_frei()
    try:
        return Response(await pcdienst.bildschirm(), media_type="image/png", headers={"Cache-Control": "no-store"})
    except pcdienst.PcFehler as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/pc/schnell")
async def pc_schnell(s: PcSchnell, request: Request) -> dict:
    _gleiche_herkunft(request)
    _pc_frei(schreiben=True)
    if s.aktion not in PC_SCHNELL:
        raise HTTPException(400, "Diese Aktion gibt es hier nicht.")
    try:
        r = await pcdienst.aktion("system", {"aktion": s.aktion, "schritte": 5})
    except pcdienst.PcFehler as exc:
        return {"ok": False, "text": str(exc)}
    if r.get("ok"):
        pcdienst.vermerken(str(r.get("beschreibung") or s.aktion), str(r.get("text") or ""), "system")
    return {"ok": bool(r.get("ok")), "text": r.get("text") or ""}


async def _pc_rueckfrage(aktion: str, request: Request) -> dict:
    """Legt eine Rückfrage an (wie beim Modell-Werkzeug) — der Helfer führt erst nach Eikes Tipp aus."""
    _gleiche_herkunft(request)
    _pc_frei(schreiben=True)
    try:
        probe = await pcdienst.aktion("system", {"aktion": aktion}, probe=True)
    except pcdienst.PcFehler as exc:
        return {"ok": False, "text": str(exc)}
    if not probe.get("ok"):
        return {"ok": False, "text": probe.get("text") or "Das geht nicht."}
    beschreibung = str(probe.get("beschreibung") or aktion)
    pcdienst.vormerken("system", {"aktion": aktion}, beschreibung, str(probe.get("fingerabdruck") or ""))
    return {"ok": True, "text": "Rückfrage angelegt — bestätige unten."}


@app.post("/api/pc/herunterfahren")
async def pc_herunterfahren(request: Request) -> dict:
    return await _pc_rueckfrage("herunterfahren", request)


@app.post("/api/pc/energiesparen")
async def pc_energiesparen(request: Request) -> dict:
    return await _pc_rueckfrage("energiesparen", request)


PC_LESEN = {"messwerte", "fenster", "programme", "dateien", "prozesse", "zwischenablage"}
# Was die PC-Seite selbst auslösen darf. Der Helfer stuft jede Aktion ein; „bestätigen“ wird hier nur zur Rückfrage,
# ausgeführt wird erst nach Eikes Tipp (`pcdienst.entscheiden`). Ohne Browser-Aktionen: Fridays eigener Browser ist Sache des Modells.
PC_TUN = {"starten", "fenster_wechseln", "fenster_schliessen", "oeffnen", "verschieben", "kopieren", "umbenennen",
          "ordner_anlegen", "loeschen", "system", "meldung", "webseite", "medien", "prozess_beenden", "zwischenablage"}


@app.get("/api/pc/lesen/{was}")
async def pc_lesen(was: str, pfad: str = "", suche: str = "") -> dict:
    """Lesende Aufrufe der PC-Seite — der Helfer antwortet strukturiert, mit denselben Grenzen wie für das Modell."""
    _pc_frei()
    if was == "medien":
        return {"ok": True, "medien": dashboardpcmedien.aktuell()}
    if was not in PC_LESEN:
        raise HTTPException(404, "Das gibt es nicht.")
    try:
        return await pcdienst.lesen(f"/{was}", {"pfad": pfad[:200], "suche": suche[:200]})
    except pcdienst.PcFehler as exc:
        return {"ok": False, "text": str(exc)}


class PcTun(BaseModel):
    art: str = Field(max_length=40)
    angaben: dict = Field(default_factory=dict)


@app.post("/api/pc/tun")
async def pc_tun(t: PcTun, request: Request) -> dict:
    """Eine Aktion von der PC-Seite: sicher → sofort, heikel → Rückfrage, verboten → Text. `bestaetigt` setzt nie diese Route."""
    _gleiche_herkunft(request)
    _pc_frei(schreiben=True)
    if t.art not in PC_TUN or len(json.dumps(t.angaben, ensure_ascii=False)) > 30000:
        raise HTTPException(400, "Diese Aktion gibt es hier nicht.")
    try:
        probe = await pcdienst.aktion(t.art, t.angaben, probe=True)
        if not probe.get("ok"):
            return {"ok": False, "text": probe.get("text") or "Das geht nicht."}
        beschreibung = str(probe.get("beschreibung") or t.art)
        if probe.get("risiko") == "sicher":
            r = await pcdienst.aktion(t.art, t.angaben)
            if r.get("ok"):
                pcdienst.vermerken(beschreibung, str(r.get("text") or ""), t.art)
            return {"ok": bool(r.get("ok")), "text": r.get("text") or ""}
        if probe.get("risiko") == "bestaetigen":
            pcdienst.vormerken(t.art, t.angaben, beschreibung, str(probe.get("fingerabdruck") or ""))
            return {"ok": True, "rueckfrage": True, "text": "Rückfrage angelegt — bestätige sie."}
    except pcdienst.PcFehler as exc:
        return {"ok": False, "text": str(exc)}
    return {"ok": False, "text": probe.get("text") or "Das geht nicht."}


@app.post("/api/pc/einrichten")
def pc_einrichten(e: PcEinrichten, request: Request) -> dict:
    """Schlüssel erzeugen (oder behalten) und den Befehl für den PC bauen."""
    _gleiche_herkunft(request)
    adresse = e.adresse.strip() or os.getenv("PC_ADRESSE", "").strip()
    try:
        code = pcdienst.einrichten(adresse, neu=e.neu)
    except pcdienst.PcFehler as exc:
        raise HTTPException(400, str(exc)) from None
    url = f"{config.basis_url().rstrip('/')}/api/pc/installieren/{code}"
    return {"ok": True, "adresse": adresse, "gueltig_min": pcdienst.CODE_GILT_S // 60,
            "befehl": f"powershell -NoProfile -ExecutionPolicy Bypass -Command \"irm '{url}' | iex\""}


PC_HELFER = Path(__file__).resolve().parent.parent / "pc-helfer"


@app.get("/api/pc/installieren/{code}")
def pc_installieren(code: str) -> Response:
    """Der Installer für den PC — mit dem Schlüssel darin, deshalb nur einmal und nur 30 Minuten."""
    schluessel = pcdienst.code_einloesen(code[:64])
    if not schluessel:
        raise HTTPException(404, "Dieser Code ist abgelaufen oder schon benutzt. In Friday neu erzeugen.")
    import base64
    helfer = base64.b64encode((PC_HELFER / "friday_pc.py").read_bytes()).decode()
    quellen = [q.strip() for q in os.getenv("PC_FRIDAY_IP", "").split(",") if q.strip()] + ["127.0.0.1"]
    skript = (PC_HELFER / "installieren.ps1").read_text(encoding="utf-8")
    for platzhalter, wert in (("__SCHLUESSEL__", schluessel), ("__HELFER__", helfer),
                              ("__QUELLEN__", ",".join(quellen)), ("__PORT__", str(pcdienst.PORT))):
        skript = skript.replace(platzhalter, wert)
    return Response(skript, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})



class ClaudeSitzung(BaseModel):
    name: str = Field(default="", max_length=40)


@app.get("/api/claude/zustand")
def claude_zustand() -> dict:
    """Welche Claude-Code-Sitzungen gerade laufen (Extras → Claude). Nur lesen."""
    return claude_sitzung.zustand()


@app.post("/api/claude/starten")
def claude_starten(s: ClaudeSitzung, request: Request) -> dict:
    """Startet auf hphome eine Claude-Code-Sitzung mit Fernzugriff — nur auf Eikes Tipp
    in der App, kein Werkzeug fürs Modell."""
    _gleiche_herkunft(request)
    try:
        return {"ok": True, **claude_sitzung.starten(s.name)}
    except claude_sitzung.ClaudeFehler as exc:
        return {"ok": False, "text": str(exc)}


@app.post("/api/claude/beenden")
def claude_beenden(s: ClaudeSitzung, request: Request) -> dict:
    _gleiche_herkunft(request)
    try:
        return {"ok": True, **claude_sitzung.beenden(s.name)}
    except claude_sitzung.ClaudeFehler as exc:
        return {"ok": False, "text": str(exc)}


# ---------- Werkbank: Verbesserungen an Friday (Extras → hphome) ----------
# Starten nur auf Eikes Tipp (oder `vorhaben_starten` mit seinem Ja); dieselben Prüfungen in `werkbank.starten.starten_dict`.
# Dazu die Automatik (`werkbank.automatik`), nur wenn Eike den Hebel hier über die Route umlegt; kein Werkzeug kennt ihn.
# Fremder Origin bei POST: `frisch_halten`. Übernehmen (Live-Gang) nur per Knopf: `werkbank.uebernehmen`, Helfer prüft Positivliste.

class WerkbankStart(BaseModel):
    backend: Literal["claude", "deepseek", "opencode"] = "claude"


class WerkbankUebernahme(BaseModel):
    # StrictBool: nur ein echtes true zählt (kein "ja", keine 1). Geschützte Pfade sind im Helfer HART gesperrt —
    # ein Feld dafür gibt es nicht; unbekannte Felder (auch `geschuetzte_pfade_ok`) werden ignoriert.
    trotz_rot: StrictBool = False


_WERKBANK_LISTE_BERICHT = 4000


def _werkbank_kurz(v: dict) -> dict:
    d = {k: v.get(k) for k in ("id", "titel", "art", "quelle", "fremd", "zaehler", "erstmals", "zuletzt", "status",
                                "backend", "branch", "tests", "versuche", "gestartet", "uebernahme")}
    d["beleg"] = str(v.get("beleg") or "")[:werkbankspeicher.BELEG_MAX]
    d["bericht"] = str(v.get("bericht") or "")[:_WERKBANK_LISTE_BERICHT]
    d["diff"] = bool(v.get("diff_pfad")) and v.get("status") == "bereit"
    d["nachbesserungen"] = int(v.get("nachbesserungen") or 0)
    d["nachbesserung"] = werkbanklauf.nachbesserung_laufend(v)    # läuft gerade Nachbesserung n? (aus stand.json)
    return d


@app.get("/api/werkbank")
def werkbank_liste() -> dict:
    fach = werkbankspeicher.standard()
    alle = {v["id"]: v for v in fach.letzte(20)}
    alle.update({v["id"]: v for v in fach.offene()})
    return {"vorhaben": [_werkbank_kurz(alle[i]) for i in sorted(alle, reverse=True)],
            "heute_gestartet": fach.heute_gestartet(), "auto_heute": fach.auto_heute(),
            "auto_limit": werkbankstarter.AUTO_TAGESLIMIT, "nachbesserungen_max": werkbanklauf.MAX_NACHBESSERUNGEN}


class WerkbankEinstellung(BaseModel):
    werkbank_auto: Optional[Literal["aus", "an"]] = None
    werkbank_auto_backend: Optional[Literal["claude", "deepseek", "opencode"]] = None
    werkbank_auto_uebernehmen: Optional[Literal["aus", "an"]] = None
    werkbank_selbst_limit: Optional[Literal["aus", "an"]] = None


def _werkbank_einstellungen() -> dict:
    return {**werkbankautomatik.einstellungen(), "werkbank_auto_uebernehmen": "an" if werkbankselbst.eingeschaltet() else "aus",
            "selbst_limit": werkbankselbst.TAGESLIMIT,
            "werkbank_selbst_limit": "an" if werkbankselbst.limit_an() else "aus", "auto_heute": werkbankspeicher.standard().auto_heute(),
            "auto_limit": werkbankautomatik.AUTO_TAGESLIMIT}


@app.get("/api/werkbank/einstellungen")
def werkbank_einstellungen(antwort: Response) -> dict:
    antwort.headers["Cache-Control"] = "no-store"
    return _werkbank_einstellungen()


@app.post("/api/werkbank/einstellungen")
def werkbank_einstellungen_setzen(s: WerkbankEinstellung) -> dict:
    """Nur Eikes Hebel in der App (fremde Origins lehnt `frisch_halten` ab). Kein Werkzeug schreibt diese Einstellungen."""
    if s.werkbank_auto is None and s.werkbank_auto_backend is None and s.werkbank_auto_uebernehmen is None \
            and s.werkbank_selbst_limit is None:
        raise HTTPException(422, "werkbank_auto, werkbank_auto_backend oder werkbank_auto_uebernehmen fehlt.")
    if s.werkbank_auto_uebernehmen is not None:
        werkbankselbst.einstellen(s.werkbank_auto_uebernehmen)
    if s.werkbank_selbst_limit is not None:
        werkbankselbst.limit_einstellen(s.werkbank_selbst_limit)
    if s.werkbank_auto is not None or s.werkbank_auto_backend is not None:
        werkbankautomatik.einstellen(auto=s.werkbank_auto, backend=s.werkbank_auto_backend)
    return _werkbank_einstellungen()


@app.post("/api/werkbank/{vid}/starten")
def werkbank_starten(vid: int, s: WerkbankStart) -> dict:
    d = werkbankstarter.starten_dict(vid, s.backend)
    if not d["ok"]:
        raise HTTPException(d.get("code", 409), d["text"])
    return {"ok": True, "text": d["text"]}


@app.post("/api/werkbank/{vid}/weiter")
def werkbank_weiter(vid: int) -> dict:
    # Das Backend des Vorhabens, nicht fest Claude: Ist das Claude-Abo leer, liefe der Versuch sonst ins 403.
    v = werkbankspeicher.standard().eintrag(vid)
    d = werkbankstarter.starten_dict(vid, (v.get("backend") if v else None) or "deepseek", weiter=True)
    if not d["ok"]:
        raise HTTPException(d.get("code", 409), d["text"])
    return {"ok": True, "text": d["text"]}


@app.post("/api/werkbank/{vid}/uebernehmen")
def werkbank_uebernehmen(vid: int, s: WerkbankUebernahme | None = None) -> dict:
    """Eikes Knopf: Ergebnis live bringen (Merge, Neubau, Rückfall im Helfer). Kein Werkzeug führt hierher."""
    s = s or WerkbankUebernahme()
    d = werkbankuebernahme.uebernehmen_dict(vid, trotz_rot=s.trotz_rot)
    if not d["ok"]:
        raise HTTPException(d.get("code", 409), d["text"])
    return {"ok": True, "text": d["text"]}


@app.post("/api/werkbank/{vid}/verwerfen")
def werkbank_verwerfen(vid: int) -> dict:
    fach = werkbankspeicher.standard()
    v = fach.eintrag(vid)
    if not v:
        raise HTTPException(404, "Dieses Vorhaben gibt es nicht.")
    if v["status"] == "laeuft":
        raise HTTPException(409, "Daran wird gerade gearbeitet; verwerfen geht, sobald es fertig oder fehlgeschlagen ist.")
    if v["status"] in ("uebernommen", "verworfen"):
        raise HTTPException(409, "Das ist schon abgeschlossen.")
    fach.status_setzen(vid, "verworfen")
    return {"ok": True, "status": "verworfen"}


@app.get("/api/werkbank/{vid}/diff")
def werkbank_diff(vid: int) -> dict:
    v = werkbankspeicher.standard().eintrag(vid)
    if not v:
        raise HTTPException(404, "Dieses Vorhaben gibt es nicht.")
    if v["status"] != "bereit":
        raise HTTPException(409, "Einen Diff gibt es nur, wenn das Ergebnis bereitliegt.")
    gelesen = werkbanklauf.datei_lesen(vid, "diff.patch", werkbanklauf.DIFF_MAX) if v.get("diff_pfad") else None
    if gelesen is None:
        raise HTTPException(404, "Zu diesem Ergebnis gibt es keinen Diff.")
    return {"diff": gelesen[0].decode("utf-8", "replace"), "gekuerzt": gelesen[1]}


# ---------- hphome: den Server ansehen und verwalten (Extras → hphome) ----------
# Ausgeführt wird alles vom Helfer auf hphome (`host-helfer/hphome_helfer.py`); hier nur Aufträge.
# Das Modell löscht nur über die Werkzeuge `medien_*` (Rückfrage + Ja). Die Herkunftsprüfung sitzt global (`frisch_halten`).

def _hphome(aufruf, *args) -> dict:
    try:
        return aufruf(*args)
    except hphomedienst.HphomeFehler as exc:
        raise HTTPException(exc.status, str(exc)) from None


@app.get("/api/hphome")
def hphome_zustand() -> dict:
    return hphomedienst.zustand()


# Der feste Bestand: welche Container es gibt und wie sie gesteuert werden (ausführlich: docs/CONTAINER.md).
# Statisch, ohne Helfer und ohne Geheimnisse — der lebende Zustand steht in /api/hphome.
@app.get("/api/hphome/doku")
def hphome_doku() -> dict:
    return {"stand": containerdoku.STAND, "wege": containerdoku.WEGE,
            "container": [dict(c) for c in containerdoku.CONTAINER]}


# Logs per POST und VOR der allgemeinen Route (die wiese „logs“ sonst mit 422 ab). Ein GET könnte jede fremde Seite
# per <img src=…> auslösen — `frisch_halten` prüft die Herkunft nur bei ändernden Methoden.
@app.post("/api/hphome/container/{name}/logs")
def hphome_logs(name: str) -> dict:
    from kern.werkzeuge import katalog
    antwort = _hphome(hphomedienst.logs, name)
    # Logs können Schlüssel enthalten. Geschwärzt, bevor sie den Server verlassen; die Seite zeigt sie als Text.
    return {**antwort, "text": katalog.geheimes_schwaerzen(antwort["text"])}


@app.post("/api/hphome/container/{name}/{aktion}")
def hphome_container(name: str, aktion: Literal["neustart", "stopp", "start"]) -> dict:
    return _hphome(hphomedienst.container_aktion, name, aktion)


@app.post("/api/hphome/host/neustart")
def hphome_host_neustart() -> dict:
    return _hphome(hphomedienst.host_neustart)


@app.post("/api/hphome/platz/schaffen")
def hphome_platz_schaffen() -> dict:
    return _hphome(hphomedienst.platz_schaffen)


@app.post("/api/hphome/handy/start")
def hphome_handy_start() -> dict:
    return _hphome(hphomedienst.handy_start)


# ---------- Medien: Duplikate aufräumen, Serie/Film überall löschen (Extras → hphome → Medien) ----------
# Das Modell löscht nur über die Werkzeuge `medien_*` (Rückfrage + Ja). Die Herkunftsprüfung sitzt global (`frisch_halten`).


@app.get("/api/medien/duplikate")
def medien_duplikate() -> list[dict]:
    wurzel = mediendienst.medien_wurzel()
    treffer = mediendienst.find_duplikate(wurzel / "downloads", wurzel / "media")
    return [t.__dict__ for t in treffer]


class DuplikatLoeschenKoerper(BaseModel):
    pfade: list[str]


@app.post("/api/medien/duplikate/loeschen")
def medien_duplikate_loeschen(body: DuplikatLoeschenKoerper) -> dict:
    from pathlib import Path as P

    wurzel = mediendienst.medien_wurzel()
    downloads = (wurzel / "downloads").resolve()
    aktuell = {t.download_pfad for t in mediendienst.find_duplikate(wurzel / "downloads", wurzel / "media")}
    geloescht = []
    for roh in body.pfade:
        pfad = P(roh).resolve()
        if downloads not in pfad.parents:
            continue
        if roh not in aktuell:
            continue
        mediendienst.duplikat_loeschen(pfad)
        geloescht.append(roh)
    return {"geloescht": geloescht}


@app.get("/api/medien/serien")
async def medien_serien() -> dict:
    try:
        return {"eintraege": await sonarrdienst.serien(), "fehler": None}
    except sonarrdienst.SonarrFehler as exc:
        return {"eintraege": [], "fehler": str(exc)}


@app.get("/api/medien/filme")
async def medien_filme() -> dict:
    try:
        return {"eintraege": await radarrdienst.filme(), "fehler": None}
    except radarrdienst.RadarrFehler as exc:
        return {"eintraege": [], "fehler": str(exc)}


class LoeschenKoerper(BaseModel):
    titel: str


@app.post("/api/medien/serie/{serie_id}/loeschen")
async def medien_serie_loeschen(serie_id: int, body: LoeschenKoerper) -> dict:
    ergebnis = await mediendienst.serie_ueberall_loeschen(serie_id, body.titel)
    return ergebnis.__dict__


@app.post("/api/medien/film/{film_id}/loeschen")
async def medien_film_loeschen(film_id: int, body: LoeschenKoerper) -> dict:
    ergebnis = await mediendienst.film_ueberall_loeschen(film_id, body.titel)
    return ergebnis.__dict__


@app.get("/api/medien/serie/{serie_id}/staffeln")
async def medien_staffeln(serie_id: int) -> dict:
    try:
        serie = await sonarrdienst.serie_details(serie_id)
        return {"eintraege": serie.get("seasons", []), "fehler": None}
    except sonarrdienst.SonarrFehler as exc:
        return {"eintraege": [], "fehler": str(exc)}


@app.get("/api/medien/serie/{serie_id}/folgen")
async def medien_folgen(serie_id: int, staffel: int) -> dict:
    try:
        alle = await sonarrdienst.episoden(serie_id)
        return {"eintraege": [e for e in alle if e.get("seasonNumber") == staffel], "fehler": None}
    except sonarrdienst.SonarrFehler as exc:
        return {"eintraege": [], "fehler": str(exc)}


@app.post("/api/medien/serie/{serie_id}/staffel/{staffel_nr}/loeschen")
async def medien_staffel_loeschen(serie_id: int, staffel_nr: int, body: LoeschenKoerper) -> dict:
    ergebnis = await mediendienst.staffel_ueberall_loeschen(serie_id, staffel_nr, body.titel)
    return ergebnis.__dict__


@app.post("/api/medien/serie/{serie_id}/episode/{episode_id}/loeschen")
async def medien_episode_loeschen(serie_id: int, episode_id: int, body: LoeschenKoerper) -> dict:
    ergebnis = await mediendienst.folge_ueberall_loeschen(serie_id, episode_id, body.titel)
    return ergebnis.__dict__


@app.get("/api/schluessel")
def schluessel_uebersicht() -> dict:
    """Welche Gemini-Schlüssel wofür dienen und welche gerade am Limit sind.
    Nur die Namen, nie die Schlüssel selbst."""
    return schluesselstand.uebersicht()


class SchluesselAutomatik(BaseModel):
    an: bool


@app.post("/api/schluessel/automatik")
def schluessel_automatik_setzen(e: SchluesselAutomatik, request: Request) -> dict:
    """Automatisches Anlegen neuer Schlüssel schalten (Einstellungen)."""
    _gleiche_herkunft(request)
    schluessel_pool.automatik_setzen(e.an)
    return schluesselstand.uebersicht()


@app.post("/api/schluessel/anlegen")
def schluessel_anlegen(request: Request) -> dict:
    """Jetzt einen neuen Schlüssel anlegen — unabhängig vom Automatik-Schalter."""
    _gleiche_herkunft(request)
    ok, text = schluessel_pool.anlegen_anstossen(erzwingen=True)
    return {"ok": ok, "text": text}


# Interner Name (aus kern/main.py:_starten) → Anzeige in den Einstellungen.
PROZESS_NAMEN = {
    "rueckblick": "Rückblick nach dem Gespräch",
    "briefe": "Elternbriefe verarbeiten",
    "abgleich": "IServ-Postfach abgleichen",
    "chat_auswertung": "Hochgeladene Verläufe auswerten",
    "erinnerungen": "Erinnerungen",
    "studioplaner": "Studio: Video planen/rendern",
    "briefing": "Morgenbriefing",
    "garantiesammler": "ÜSTRA-Garantie: Fahrten sammeln",
    "fahrgastrechte": "Bahn-Fahrgastrechte",
    "hausaufgabenablage": "Hausaufgaben zerlegen",
    "goodnotesabgleich": "Goodnotes-Abgleich (WebDAV)",
    "gruppenordner": "Gruppenordner-Abgleich (WebDAV)",
    "buecherablage": "Schulbücher indexieren",
    "arbeitenablage": "Klassenarbeiten fortsetzen",
    "whatsapp": "WhatsApp (stündlich)",
    "wache": "Abfahrten im Blick",
}


@app.get("/api/prozesse")
def prozesse_uebersicht() -> dict:
    """Welche Hintergrundläufe gerade laufen oder abgestürzt sind (Einstellungen)."""
    laeufe = [{**p, "anzeige": PROZESS_NAMEN.get(p["name"], p["name"])} for p in prozesse.uebersicht()]
    return {"prozesse": laeufe}


@app.get("/api/zustand")
def zustand() -> dict:
    """Was Friday gerade kann und weiß — für die Einstellungen-Seite.
    Nur lokale Zahlen, keine Netzabfragen, damit die Seite schnell bleibt.
    Jeder Block einzeln abgesichert: fällt einer aus, steht der Rest trotzdem da."""
    d: dict = {"version": config.VERSION, "phase": config.PHASE,
               "stimmmodell": config.stimmmodell(), "textmodell": config.textmodell(),
               "stimme": config.stimme(),
               "schluessel": {"gespraech": len(config.gespraech_schluessel_liste()),
                              "hintergrund": len(config.hintergrund_schluessel_liste()),
                              "graph": bool(os.getenv("GRAPH_GEMINI_API_KEY", "").strip())},
               "hintergrund": os.getenv("FRIDAY_HINTERGRUND", "an").strip().lower() != "aus"}
    try:
        d["konnektoren"] = [{"name": k["name"], "aktiv": k["aktiv"], "zugang": bool(k["benutzer"]),
                             "schreiben": k["schreiben"]} for k in konnektoren.uebersicht()]
    except Exception:
        d["konnektoren"] = []
    try:
        g, p = gspeicher.standard(), speicher.standard()
        d["gedaechtnis"] = {"fakten": len(g.fakten()), "vorschlaege": len(g.vorschlaege()),
                            "graph": p.graph_stand(),
                            "pause": graph.pause_bis() if graph.pausiert() else ""}
    except Exception:
        d["gedaechtnis"] = {}
    for name, quelle in (("briefe", lambda: ablage.standard().stand()),
                         ("mails", lambda: postablage.standard().stand()),
                         ("verlaeufe", lambda: chatablage.standard().stand())):
        try:
            d[name] = quelle()
        except Exception:
            d[name] = {}
    try:
        d["erinnerungen"] = erinnerungen.standard().stand()
    except Exception:
        d["erinnerungen"] = {}
    try:
        d["push"] = {"geraete": len(push.geraete()), "kontakt": push.KONTAKT}
    except Exception:
        d["push"] = {"geraete": 0}
    try:
        offen = [e for e in umgebung.alle() if umgebung.ist_aktiv(e)]
        d["umgebung"] = {"hier": umgebung.satz(), "offen": len(offen)}
    except Exception:
        d["umgebung"] = {}
    return d


# ---------- Spotify: einmalige Erlaubnis holen (OAuth) ----------

# ---------- Studio: einmalige Erlaubnis für den YouTube-Kanal ----------

@app.get("/api/studio/start")
def studio_start():
    """Schickt Eike zu Google, damit er den Zugriff auf den Kanal erlaubt."""
    try:
        return RedirectResponse(studio.anmelde_url(), status_code=307)
    except studio.StudioFehler as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/api/studio/rueck")
async def studio_rueck(code: str = "", state: str = "", error: str = ""):
    """Der Rückläufer von Google. Das Ergebnis steht danach auf der Freigaben-Seite."""
    if error:
        konnektoren.vermerken("studio", False, f"Google hat den Zugriff nicht erlaubt ({error}).")
    else:
        try:
            await studio.code_einloesen(code, state)
            konnektoren.vermerken("studio", True, f"Mit dem Kanal {studio.KANAL} verbunden.")
        except studio.StudioFehler as exc:
            konnektoren.vermerken("studio", False, str(exc))
    return RedirectResponse("/#freigaben", status_code=303)


def _studio_lesen(ordner) -> list[dict]:
    eintraege = []
    for datei in sorted(ordner.glob("*.json"), reverse=True):
        if datei.name.endswith(".neu.json"):
            continue
        try:
            d = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue                       # halb geschriebene Datei: beim nächsten Mal
        d["kennung"] = datei.stem
        d.pop("schluessel", None)          # Zugangsdaten gehen nie an den Browser
        eintraege.append(d)
    return eintraege


@app.get("/api/studio/wartend")
def studio_wartend() -> dict:
    """Was fertig ist UND was gerade entsteht — für den Studio-Tab.

    Ohne die laufenden Aufträge sähe Eike minutenlang eine leere Seite und
    wüsste nicht, ob überhaupt etwas passiert.
    """
    basis = config.daten_ordner() / "studio"
    return {"videos": _studio_lesen(basis / "fertig"),
            "inArbeit": _studio_lesen(basis / "auftraege")}


@app.get("/api/studio/video/{kennung}")
def studio_video(kennung: str):
    """Das fertige Video selbst, damit Eike es vor der Freigabe ansehen kann."""
    sicher = "".join(z for z in kennung if z.isalnum() or z in "-_")[:100]
    pfad = config.daten_ordner() / "studio" / "fertig" / f"{sicher}.mp4"
    if not sicher or not pfad.is_file():
        raise HTTPException(404, "Dieses Video gibt es nicht.")
    return FileResponse(pfad, media_type="video/mp4")


async def _studio_zahlen(holen) -> dict:
    """Statistik für den Studio-Tab — mit Zustand statt Fehlercode.

    Der Tab soll bei jedem Zustand etwas Sinnvolles zeigen: „aus“ (Freigabe
    fehlt), „getrennt“ (kein oder abgelaufener YouTube-Zugang, mit Knopf zum
    Neuverbinden), „fehler“ (YouTube antwortet nicht) oder „ok“ mit den Zahlen.
    """
    if not konnektoren.aktiv("studio"):
        return {"zustand": "aus", "text": "Die Freigabe „Studio“ ist aus. Einschalten auf der Freigaben-Seite."}
    if not studio.verbunden():
        return {"zustand": "getrennt", "text": "Das Studio ist noch nicht mit YouTube verbunden."}
    try:
        return {"zustand": "ok", **await holen()}
    except studio.ZugangFehler as exc:
        return {"zustand": "getrennt", "text": str(exc)}
    except studio.StudioFehler as exc:
        return {"zustand": "fehler", "text": str(exc)}
    except Exception as exc:
        return {"zustand": "fehler", "text": f"YouTube antwortet gerade nicht ({type(exc).__name__})."}


def _tage(tage: int) -> int:
    if tage not in studiostatistik.TAGE_ERLAUBT:
        raise HTTPException(400, "Zeitraum: 7, 28, 90, 365 oder 0 (alles).")
    return tage


@app.get("/api/studio/statistik/uebersicht")
async def studio_statistik_uebersicht(tage: int = 28, frisch: bool = False) -> dict:
    tage = _tage(tage)
    return await _studio_zahlen(lambda: studiostatistik.uebersicht(tage, frisch))


@app.get("/api/studio/statistik/videos")
async def studio_statistik_videos(tage: int = 28, frisch: bool = False) -> dict:
    tage = _tage(tage)
    return await _studio_zahlen(lambda: studiostatistik.videos(tage, frisch))


@app.get("/api/studio/statistik/publikum")
async def studio_statistik_publikum(tage: int = 28, frisch: bool = False) -> dict:
    tage = _tage(tage)
    return await _studio_zahlen(lambda: studiostatistik.publikum(tage, frisch))


@app.get("/api/studio/statistik/video/{kennung}")
async def studio_statistik_video(kennung: str, frisch: bool = False) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", kennung):
        raise HTTPException(400, "Das ist keine Video-Kennung.")
    return await _studio_zahlen(lambda: studiostatistik.video(kennung, frisch))


@app.get("/api/studio/themen")
def studio_themen() -> dict:
    """Die bisherigen Themen des Planers, neueste zuerst — damit man sieht, was schon dran war."""
    return {"themen": list(reversed(studioplaner.historie()))[:60]}


@app.post("/api/studio/trennen")
def studio_trennen() -> dict:
    studio.trennen()
    konnektoren.vermerken("studio", False, "Verbindung zum Kanal getrennt.")
    return {"ok": True}


class StudioHochladen(BaseModel):
    kennung: str = Field(max_length=64)


@app.post("/api/studio/hochladen")
async def studio_hochladen_knopf(s: StudioHochladen, request: Request) -> dict:
    """Eikes Tipp in der Werkstatt: das gewählte wartende Video sofort veröffentlichen.

    Kein Werkzeug fürs Modell — die App fragt vorher selbst nach (wie beim Abschicken
    der Garantie), dieselbe Prüfung wie beim Zuruf an Friday (studio_hochladen)."""
    _gleiche_herkunft(request)
    if not konnektoren.benutzbar("studio"):
        raise HTTPException(403, "Die Freigabe „Studio“ ist aus oder nicht verbunden.")
    if not konnektoren.schreiben_erlaubt("studio"):
        raise HTTPException(403, "Schreibrecht für „Studio“ ist aus — Freigaben-Seite.")
    ordner = config.daten_ordner() / "studio" / "fertig"
    wartende = [d for d in _studio_lesen(ordner) if d.get("status") == "fertig" and not d.get("hochgeladen")]
    eintrag = next((d for d in wartende if d["kennung"] == s.kennung), None)
    if eintrag is None:
        return {"ok": False, "text": "Dieses Video wartet nicht mehr — Seite neu laden."}
    titel = eintrag.get("titel") or eintrag.get("thema", "")
    video = ordner / str(eintrag.get("video") or f"{eintrag['kennung']}.mp4")
    try:
        ergebnis = await studio.hochladen(video, titel=titel, beschreibung=eintrag.get("beschreibung", ""),
                                          schlagworte=eintrag.get("schlagworte") or [], oeffentlich=True)
    except studio.StudioFehler as exc:
        return {"ok": False, "text": str(exc)}
    eintrag["hochgeladen"] = config.jetzt().isoformat(timespec="seconds")
    eintrag["url"] = ergebnis["url"]
    (ordner / f"{eintrag['kennung']}.json").write_text(
        json.dumps(eintrag, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "text": f"Veröffentlicht: {ergebnis['url']}", "url": ergebnis["url"]}


@app.get("/api/spotify/start")
def spotify_start():
    """Schickt Eike zu Spotify, damit er den Zugriff erlaubt."""
    try:
        return RedirectResponse(spotify.anmelde_url(), status_code=307)
    except spotify.SpotifyFehler as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/api/spotify/rueck")
async def spotify_rueck(code: str = "", state: str = "", error: str = ""):
    """Der Rückläufer von Spotify. Das Ergebnis steht danach auf der Freigaben-Seite."""
    if error:
        konnektoren.vermerken("spotify", False, f"Spotify hat den Zugriff nicht erlaubt ({error}).")
    else:
        try:
            await spotify.code_einloesen(code, state)
            konnektoren.vermerken("spotify", True, "Mit Spotify verbunden.")
        except spotify.SpotifyFehler as exc:
            konnektoren.vermerken("spotify", False, str(exc))
    return RedirectResponse("/#freigaben", status_code=303)


class Geraete(BaseModel):
    # Nur diese Geräte darf Friday als Ziel einer Wiedergabe wählen.
    geraete: list[dict] = Field(default_factory=list, max_length=50)


@app.get("/api/spotify/geraete")
async def spotify_geraete() -> dict:
    try:
        return {"geraete": await spotify.geraete()}
    except spotify.SpotifyFehler as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/spotify/geraete")
def spotify_geraete_setzen(g: Geraete) -> dict:
    spotify.erlaubte_setzen(g.geraete)
    return {"ok": True, "anzahl": len(spotify.erlaubte())}


@app.post("/api/spotify/trennen")
def spotify_trennen() -> dict:
    spotify.trennen()
    return {"ok": True}


# ---------- Benachrichtigungen (Web Push) ----------

class PushAnmeldung(BaseModel):
    anmeldung: dict
    geraet: str = Field("", max_length=80)


class PushAbmeldung(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1000)


@app.get("/api/push/schluessel")
def push_schluessel() -> dict:
    """Der öffentliche VAPID-Schlüssel — der private bleibt auf dem Server."""
    return {"schluessel": push.oeffentlicher_schluessel()}


@app.post("/api/push/anmelden")
def push_anmelden(a: PushAnmeldung) -> dict:
    try:
        anzahl = push.anmelden(a.anmeldung, a.geraet)
    except ValueError:
        raise HTTPException(400, "Die Anmeldung des Browsers war unvollständig.") from None
    return {"ok": True, "geraete": anzahl}


@app.post("/api/push/abmelden")
def push_abmelden(a: PushAbmeldung) -> dict:
    return {"ok": True, "entfernt": push.abmelden(a.endpoint)}


@app.get("/api/push/geraete")
def push_geraete() -> list[dict]:
    return push.geraete()


class PushTest(BaseModel):
    # Verzögert, damit Eike die App schließen kann: iOS zeigt ein Banner nur
    # zuverlässig, wenn die App nicht gerade im Vordergrund ist.
    verzoegerung: int = Field(0, ge=0, le=120)


@app.post("/api/push/test")
async def push_test(t: PushTest | None = None) -> dict:
    """Geht bewusst an `benachrichtigungen` vorbei, direkt auf `push`: Die Testnachricht
    prüft die TECHNIK (Gerät, Schlüssel, Apple). Würde sie an einem ausgeschalteten
    Schalter oder an der Ruhezeit hängenbleiben, prüfte sie das Gegenteil davon."""
    warten = (t.verzoegerung if t else 0)
    text = "Das ist eine Testnachricht — Benachrichtigungen funktionieren."
    if not push.geraete():
        return {"gesendet": 0, "geraete": 0, "text": "Es ist kein Gerät angemeldet."}
    if warten:
        async def spaeter():
            await asyncio.sleep(warten)
            await asyncio.to_thread(push.senden, "Friday", text, "/#einstellungen", "test")
        asyncio.create_task(spaeter())
        return {"gesendet": 0, "geplant": warten,
                "text": f"Kommt in {warten} Sekunden — schließ die App jetzt."}
    ergebnis = await asyncio.to_thread(push.senden, "Friday", text, "/#einstellungen", "test")
    ergebnis["text"] = ("Testnachricht ist raus." if ergebnis["gesendet"]
                        else f"Der Push-Dienst hat abgelehnt — {ergebnis.get('grund') or 'ohne Grund'}")
    return ergebnis


# ---------- ÜSTRA-Garantie ----------
# Eike wählt die Fahrt und seine Haltestellen und lädt sein Ticket hoch; Friday
# prüft alles andere. Abschicken nur mit eigener Freigabe und Knopfdruck (kern/garantie/abschicken.py).

GARANTIE = "uestra_garantie"
TICKET_TYPEN = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
MAX_TICKET = 10 * 1024 * 1024


class GarantieAngaben(BaseModel):
    anrede: Optional[str] = Field(None, max_length=20)
    vorname: Optional[str] = Field(None, max_length=80)
    nachname: Optional[str] = Field(None, max_length=80)
    email: Optional[str] = Field(None, max_length=120)
    strasse: Optional[str] = Field(None, max_length=120)
    plz: Optional[str] = Field(None, max_length=10)
    ort: Optional[str] = Field(None, max_length=80)
    fahrkarte: Optional[str] = Field(None, max_length=40)
    gueltig_bis: Optional[str] = Field(None, max_length=10)
    kontoinhaber: Optional[str] = Field(None, max_length=120)
    iban: Optional[str] = Field(None, max_length=42)
    abo_nummer: Optional[str] = Field(None, max_length=20, pattern=r"^\d*$")


class GarantieVorbereiten(BaseModel):
    fahrt: str = Field(min_length=6, max_length=20)
    start: str = Field(min_length=5, max_length=40)      # Kennung des Halts, z.B. de:03241:5844
    ziel: str = Field(min_length=5, max_length=40)
    profil: int = Field(0, ge=0)                         # für wen: 0 = Eike, sonst ein Freund


class GarantieProfil(BaseModel):
    """Ein Freund. Alles optional: beim Ändern zählt nur, was mitgeschickt wird."""
    name: Optional[str] = Field(None, max_length=60)
    anrede: Optional[str] = Field(None, max_length=20)
    vorname: Optional[str] = Field(None, max_length=80)
    nachname: Optional[str] = Field(None, max_length=80)
    email: Optional[str] = Field(None, max_length=120)
    strasse: Optional[str] = Field(None, max_length=120)
    plz: Optional[str] = Field(None, max_length=10)
    ort: Optional[str] = Field(None, max_length=80)
    abo_nummer: Optional[str] = Field(None, max_length=20)
    kundenkarte: Optional[str] = Field(None, max_length=12)
    auszahlung: Optional[str] = Field(None, max_length=10)
    kontoinhaber: Optional[str] = Field(None, max_length=120)
    iban: Optional[str] = Field(None, max_length=42)
    anteil: Optional[float] = None
    vollmacht: Optional[bool] = None
    archiviert: Optional[bool] = None
    notiz: Optional[str] = Field(None, max_length=500)


class GarantieErledigt(BaseModel):
    vorgang: str = Field(min_length=4, max_length=12)
    erledigt: bool


class GarantieStatus(BaseModel):
    status: Literal["abgeschickt", "verworfen", "offen"]


def _garantie_an() -> None:
    """Ohne Freigabe kein Kontakt zu fremden Diensten."""
    if not konnektoren.benutzbar(GARANTIE):
        raise HTTPException(409, "Die Freigabe „ÜSTRA-Garantie“ ist aus.")


# ---------- Noten (Schule → Noten) ----------
# Frei aufgebaut: Fächer mit eigenen Bereichen und Gewichtungen, Noten mit
# optionalem Anteil. Gerechnet wird in notenablage; hier nur Ein- und Ausgabe.

class BereichEin(BaseModel):
    id: Optional[int] = None
    name: str = Field("", max_length=200)
    gewicht: float = 50


class FachEin(BaseModel):
    halbjahr: Optional[str] = None       # für welches Semester die Bereiche gelten
    fuer_alle: bool = False              # … oder gleich für alle belegten Semester des Fachs
    ziel: Optional[float] = None
    notiz: str = Field("", max_length=1000)
    bereiche: Optional[list[BereichEin]] = Field(None, max_length=20)


class FachWahl(BaseModel):
    id: Optional[int] = None
    name: str = Field("", max_length=200)
    pruefung: int = 0
    stunden: Optional[int] = None
    halbjahre: list[str] = Field(default_factory=list, max_length=4)


class FachWahlListe(BaseModel):
    faecher: list[FachWahl] = Field(max_length=40)


class AbiturEin(BaseModel):
    pruefungen: dict[str, Optional[float]] = Field(default_factory=dict)
    ausgeschlossen: list[str] = Field(default_factory=list, max_length=200)


class NoteEin(BaseModel):
    bereich_id: Optional[int] = None
    halbjahr: str = ""
    titel: str = Field("", max_length=200)
    datum: str = ""
    skala: str = "punkte"
    wert: Optional[float] = None
    max: float = 15
    anteil: Optional[float] = None
    faktor: float = 1
    notiz: str = Field("", max_length=1000)


def _noten_pruefen(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _fach_oder_404(fid: int) -> dict:
    f = notenablage.standard().fach(fid)
    if not f:
        raise HTTPException(404, "Dieses Fach gibt es nicht (mehr).")
    return f


@app.get("/api/noten")
def noten_uebersicht() -> dict:
    return notenablage.standard().uebersicht()


@app.put("/api/noten/fachwahl")
def fachwahl_setzen(d: FachWahlListe) -> dict:
    """Die ganze Fächerwahl auf einmal: welche Fächer, welche Rolle, welche Semester."""
    wahl = _noten_pruefen(notenablage.fachwahl_pruefen, [f.model_dump() for f in d.faecher])
    _noten_pruefen(notenablage.standard().fachwahl_setzen, wahl)
    return notenablage.standard().uebersicht()


@app.put("/api/noten/abitur")
def abitur_setzen(d: AbiturEin) -> dict:
    """Prüfungsergebnisse (P1–P5) und abgewählte Kurse für die Abitur-Schätzung."""
    einst = _noten_pruefen(notenablage.abitur_pruefen, d.model_dump())
    notenablage.standard().abitur_setzen(einst)
    return notenablage.standard().uebersicht()


@app.put("/api/noten/faecher/{fid}")
def fach_aendern(fid: int, d: FachEin) -> dict:
    """Ziel und Notiz eines Fachs; Bereiche (mit Gewichtung) je Semester."""
    _fach_oder_404(fid)
    fach = _noten_pruefen(notenablage.fach_pruefen, d.model_dump())
    bereiche = None if d.bereiche is None else _noten_pruefen(
        notenablage.bereiche_pruefen, [b.model_dump() for b in d.bereiche])
    _noten_pruefen(notenablage.standard().fach_aendern, fid, fach, d.halbjahr, bereiche, d.fuer_alle)
    return {"ok": True}


@app.post("/api/noten/faecher/{fid}/eintraege")
def note_anlegen(fid: int, d: NoteEin) -> dict:
    _fach_oder_404(fid)
    note = _noten_pruefen(notenablage.eintrag_pruefen, d.model_dump())
    return {"id": _noten_pruefen(notenablage.standard().eintrag_neu, fid, d.bereich_id, note)}


@app.put("/api/noten/eintraege/{nid}")
def note_aendern(nid: int, d: NoteEin) -> dict:
    alt = notenablage.standard().eintrag(nid)
    if not alt:
        raise HTTPException(404, "Diese Note gibt es nicht (mehr).")
    note = _noten_pruefen(notenablage.eintrag_pruefen, d.model_dump())
    _noten_pruefen(notenablage.standard().eintrag_aendern, nid, alt["fach_id"], d.bereich_id, note)
    return {"ok": True}


@app.delete("/api/noten/eintraege/{nid}")
def note_entfernen(nid: int) -> dict:
    if not notenablage.standard().eintrag(nid):
        raise HTTPException(404, "Diese Note gibt es nicht (mehr).")
    notenablage.standard().eintrag_loeschen(nid)
    return {"ok": True}


# ---------- Klassenarbeiten (Schule → Arbeiten) ----------
# Hochladen (Fotos oder ein PDF), von Gemini lesen lassen, in Schule → Noten eintragen, als PDF zurück.

class ArbeitEin(BaseModel):
    fach_id: Optional[int] = None
    halbjahr: str = ""
    datum: str = ""
    titel: str = Field("", max_length=200)
    art: str = "klausur"
    skala: Optional[str] = None
    wert: Optional[float] = None
    max: Optional[float] = None
    bereich_id: Optional[int] = None
    eintragen: bool = True               # False: nur ablegen, ohne Note


def _arbeit_oder_404(aid: int) -> dict:
    a = arbeitenablage.standard().arbeit(aid)
    if not a:
        raise HTTPException(404, "Diese Arbeit gibt es nicht (mehr).")
    return a


def _arbeit_dict(a: dict) -> dict:
    namen = {f["id"]: f["name"] for f in notenablage.standard().uebersicht()["faecher"]}
    return arbeitenablage.uebersicht(a, namen)


@app.get("/api/arbeiten")
def arbeiten_liste() -> dict:
    uebersicht = notenablage.standard().uebersicht()
    namen = {f["id"]: f["name"] for f in uebersicht["faecher"]}
    return {"arbeiten": [arbeitenablage.uebersicht(a, namen) for a in arbeitenablage.standard().alle()],
            "faecher": [{"id": f["id"], "name": f["name"], "halbjahre": f["halbjahre"]} for f in uebersicht["faecher"]],
            "frei": konnektoren.benutzbar(arbeitenablage.KONNEKTOR)}


@app.get("/api/arbeiten/muster")
def arbeiten_muster() -> dict:
    """Wiederkehrende Fehlerarten je Person und Fach, aus allen hochgeladenen Arbeiten
    automatisch zusammengefasst (siehe kern/schule/arbeiten.muster_aktualisieren)."""
    namen = {f["id"]: f["name"] for f in notenablage.standard().uebersicht()["faecher"]}
    return {"muster": [{**m, "fach": namen.get(m["fach_id"]) or m["fach_text"]}
                       for m in arbeitenablage.standard().muster_liste()]}


@app.post("/api/arbeiten")
async def arbeit_hochladen(dateien: list[UploadFile]) -> dict:
    roh = []
    gesamt = 0
    for nummer, datei in enumerate(dateien, start=1):
        daten = await datei.read(arbeitenablage.MAX_PDF + 1)
        gesamt += len(daten)
        if gesamt > arbeitenablage.MAX_PDF:
            raise HTTPException(413, "Die Dateien sind zusammen größer als 100 MB.")
        roh.append((datei.filename or f"Seite {nummer}", daten))
    try:
        aid = await asyncio.to_thread(arbeitenablage.hochladen, roh)
    except arbeitenablage.UploadFehler as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "id": aid, "gestartet": arbeitenablage.lesen_starten(aid)}


@app.get("/api/arbeiten/{aid}/seite/{nr}")
def arbeit_seite(aid: int, nr: int, klein: int = 0) -> Response:
    _arbeit_oder_404(aid)
    bild = arbeitenablage.standard().seite(aid, nr)
    if not bild:
        raise HTTPException(404, "Diese Seite gibt es nicht.")
    return Response(arbeitenablage.klein(bild) if klein else bild, media_type="image/jpeg")


@app.get("/api/arbeiten/{aid}/pdf")
async def arbeit_pdf(aid: int) -> Response:
    a = _arbeit_oder_404(aid)
    daten = await asyncio.to_thread(arbeitenablage.pdf_bytes, aid)
    if not daten:
        raise HTTPException(404, "Von dieser Arbeit liegen keine Seiten mehr vor.")
    fach = _arbeit_dict(a)["fach"]
    name = arbeitenablage.dateiname(fach, a)
    return Response(daten, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=\"Arbeit-{aid}.pdf\"; filename*=UTF-8\'\'{quote(name)}.pdf"})


@app.put("/api/arbeiten/{aid}")
def arbeit_speichern(aid: int, d: ArbeitEin) -> dict:
    """Angaben zur Arbeit setzen: mit `eintragen` steht die Note danach unter Schule → Noten (neu oder geändert)."""
    a = _arbeit_oder_404(aid)
    if arbeitenablage.laeuft(aid):
        raise HTTPException(409, "Friday liest die Arbeit gerade — bitte kurz warten.")
    halbjahr = d.halbjahr or (notenablage.halbjahr_aus_datum(d.datum) or "")
    angaben = {"fach_id": d.fach_id, "halbjahr": halbjahr, "datum": d.datum, "titel": " ".join(d.titel.split()),
               "art": d.art, "skala": d.skala, "wert": d.wert, "max": d.max, "bereich_id": d.bereich_id}
    if d.eintragen:
        return _arbeit_dict(_noten_pruefen(arbeitenablage.eintragen, aid, angaben))
    if a["note_id"]:
        raise HTTPException(409, "Die Note ist schon eingetragen — ändere sie dort, oder speichere hier mit „Eintragen“.")
    if d.art not in arbeitenablage.ARTEN:
        raise HTTPException(422, "Unbekannte Art der Arbeit.")
    arbeitenablage.standard().setzen(aid, fach_id=d.fach_id, halbjahr=halbjahr, datum=d.datum, titel=angaben["titel"],
                                     art=d.art, status="abgelegt", hinweise=[])
    return _arbeit_dict(_arbeit_oder_404(aid))


@app.post("/api/arbeiten/{aid}/lesen")
async def arbeit_lesen(aid: int) -> dict:
    a = _arbeit_oder_404(aid)
    if a["status"] == "eingetragen":
        raise HTTPException(409, "Die Arbeit ist schon eingetragen — noch einmal lesen würde deine Angaben überschreiben.")
    if not arbeitenablage.lesen_starten(aid):
        raise HTTPException(403, "Die Freigabe „Klassenarbeiten auswerten“ ist aus. Einschalten auf der Freigaben-Seite.")
    return {"ok": True}


@app.delete("/api/arbeiten/{aid}")
def arbeit_loeschen(aid: int, note: int = 0) -> dict:
    """Arbeit und Seiten löschen. Die eingetragene Note bleibt — außer mit note=1."""
    a = _arbeit_oder_404(aid)
    if arbeitenablage.laeuft(aid):
        raise HTTPException(409, "Friday liest die Arbeit gerade — bitte kurz warten.")
    if note and a["note_id"] and notenablage.standard().eintrag(a["note_id"]):
        notenablage.standard().eintrag_loeschen(a["note_id"])
    arbeitenablage.standard().loeschen(aid)
    return {"ok": True}


# ---------- Hausaufgaben (Extras → HA) ----------
# Nur lesend. Die Auswertung (Fach, Seiten, Aufgaben, machbar?) läuft im
# Hintergrund; die Seite fragt nach, bis sie fertig ist.

@app.get("/api/hausaufgaben")
async def hausaufgaben_stand(tage: int = 7) -> dict:
    if not konnektoren.benutzbar(hausaufgabenablage.KONNEKTOR):
        return {"aktiv": False, "tage": tage, "eintraege": [], "weitere": False, "auswertung_laeuft": False}
    try:
        stand = await hausaufgabenablage.uebersicht(tage)
    except schulmanager.AnmeldeFehler as exc:
        raise HTTPException(502, str(exc))
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        raise HTTPException(502, f"Der Schulmanager ist gerade nicht erreichbar ({type(exc).__name__}).")
    return {"aktiv": True, **stand}


def _eintrag_oder_404(hid: str) -> dict:
    d = hausaufgabenablage.detail(hid)
    if not d:
        raise HTTPException(404, "Diese Hausaufgabe kennt Friday nicht.")
    return d


def _bearbeiten_frei() -> None:
    if not konnektoren.benutzbar(hausaufgabenablage.BEARBEITEN):
        raise HTTPException(403, "Die Freigabe „Hausaufgaben bearbeiten“ ist aus. Einschalten auf der Freigaben-Seite.")


@app.get("/api/hausaufgaben/eintrag/{hid}")
def hausaufgabe(hid: str) -> dict:
    return _eintrag_oder_404(hid)


@app.post("/api/hausaufgaben/eintrag/{hid}/bearbeiten")
async def hausaufgabe_bearbeiten(hid: str) -> dict:
    _bearbeiten_frei()
    _eintrag_oder_404(hid)
    hausaufgabenablage.bearbeitung_starten(hid)
    return {"ok": True}


@app.get("/api/hausaufgaben/eintrag/{hid}/bild/{name}")
def hausaufgabe_bild(hid: str, name: str):
    if not re.fullmatch(r"bild-\d+\.(png|jpg|webp)", name):
        raise HTTPException(404, "Kein Bild.")
    pfad = hausaufgabenablage.standard().ordner_fuer(hid) / name
    if not pfad.is_file():
        raise HTTPException(404, "Kein Bild.")
    return FileResponse(pfad)


# Goodnotes: Stand, Abgleich, Ordner ausschließen, suchen, Seiten ansehen.

class OrdnerWahl(BaseModel):
    ausgeschlossen: list[str] = Field(default_factory=list, max_length=200)


@app.get("/api/goodnotes")
def goodnotes_stand() -> dict:
    a = goodnotesablage.standard()
    aus = a.ausgeschlossen()
    ordner: dict[str, dict] = {}
    hefte = []
    for h in a.hefte():
        o = ordner.setdefault(h["ordner"], {"name": h["ordner"], "hefte": 0, "seiten": 0, "gelesen": 0,
                                            "ausgeschlossen": goodnotesablage.ausgeschlossen(h["ordner"], aus)})
        o["hefte"] += 1
        o["seiten"] += h["seiten"]
        o["gelesen"] += h["gelesen"] or 0
        hefte.append({k: h[k] for k in ("id", "ordner", "name", "seiten", "geaendert", "status", "fehler")}
                     | {"gelesen": h["gelesen"] or 0})
    z = goodnotesabgleich.zustand
    return {"frei": konnektoren.benutzbar(goodnotesabgleich.KONNEKTOR), "wurzel": goodnotesabgleich.wurzel(),
            "stand": a.stand(), "ordner": sorted(ordner.values(), key=lambda o: o["name"].casefold()),
            "hefte": hefte, "laeuft": goodnotesabgleich.laeuft(), "phase": z["phase"], "fehler": z["fehler"],
            "ergebnis": z["ergebnis"], "letzter": z["letzter"]}


def _goodnotes_frei() -> None:
    if not konnektoren.benutzbar(goodnotesabgleich.KONNEKTOR):
        raise HTTPException(403, "Die Freigabe „Goodnotes-Hefte“ ist aus. Einschalten auf der Freigaben-Seite.")


@app.post("/api/goodnotes/abgleichen")
async def goodnotes_abgleichen() -> dict:
    _goodnotes_frei()
    goodnotesabgleich.starten()
    return {"ok": True}


@app.post("/api/goodnotes/ordner")
def goodnotes_ordner(w: OrdnerWahl) -> dict:
    goodnotesablage.standard().ausschliessen([o.strip("/") for o in w.ausgeschlossen])
    return {"ok": True, "ausgeschlossen": goodnotesablage.standard().ausgeschlossen()}


@app.get("/api/goodnotes/suche")
def goodnotes_suche(q: str, fach: str = "") -> dict:
    _goodnotes_frei()
    return {"treffer": goodnotesablage.standard().suchen(q[:200], fach[:40], 20)}


@app.get("/api/goodnotes/seite/{hid}/{nr}")
async def goodnotes_seite(hid: int, nr: int):
    _goodnotes_frei()
    a = goodnotesablage.standard()
    s = a.seite(hid, nr)
    if not s or goodnotesablage.ausgeschlossen(s["ordner"], a.ausgeschlossen()):
        raise HTTPException(404, "Diese Seite gibt es nicht.")
    daten = await asyncio.to_thread(goodnotesabgleich.seitenbild, hid, nr)
    if not daten:
        raise HTTPException(404, "Die Seite lässt sich nicht anzeigen.")
    return Response(daten, media_type="image/jpeg")


@app.get("/api/goodnotes/text/{hid}/{nr}")
def goodnotes_text(hid: int, nr: int) -> dict:
    _goodnotes_frei()
    a = goodnotesablage.standard()
    s = a.seite(hid, nr)
    if not s or goodnotesablage.ausgeschlossen(s["ordner"], a.ausgeschlossen()):
        raise HTTPException(404, "Diese Seite gibt es nicht.")
    return {k: s[k] for k in ("heft", "nr", "ordner", "name", "titel", "datum", "art", "text", "status",
                              "heft_seiten")}


# Lernen (Schule → Lernen): zieht Noten, Arbeiten-Muster, Gruppenordner und Goodnotes
# je Fach zusammen — liest nur, gleicht nichts ab, ruft kein Gemini auf.

@app.get("/api/lernen")
def lernen_liste() -> dict:
    return {"faecher": lernenablage.liste()}


@app.get("/api/lernen/{fach_id}")
def lernen_fach(fach_id: int) -> dict:
    d = lernenablage.fach(fach_id)
    if not d:
        raise HTTPException(404, "Dieses Fach gibt es nicht.")
    return d


# Gruppenordner (IServ-WebDAV): Kursmaterialien abgleichen, zusammenfassen, durchsuchen.

@app.get("/api/gruppenordner")
def gruppenordner_stand() -> dict:
    namen = {f["id"]: f["name"] for f in notenablage.standard().uebersicht()["faecher"]}
    kurse: dict[str, dict] = {}
    for d in gruppenordnerablage.standard().alle():
        k = kurse.setdefault(d["kurs_text"], {"kurs": d["kurs_text"], "fach": namen.get(d["fach_id"]) or "",
                                              "dateien": 0, "gelesen": 0})
        k["dateien"] += 1
        k["gelesen"] += d["status"] == "gelesen"
    z = gruppenordnerablage.zustand
    return {"frei": konnektoren.benutzbar(gruppenordnerablage.KONNEKTOR), "wurzel": gruppenordnerablage.wurzel(),
            "kurse": sorted(kurse.values(), key=lambda k: k["kurs"].casefold()),
            "dateien": gruppenordnerablage.standard().alle(),
            "laeuft": gruppenordnerablage.laeuft(), "phase": z["phase"], "fehler": z["fehler"],
            "ergebnis": z["ergebnis"], "letzter": z["letzter"]}


@app.post("/api/gruppenordner/abgleichen")
async def gruppenordner_abgleichen() -> dict:
    if not konnektoren.benutzbar(gruppenordnerablage.KONNEKTOR):
        raise HTTPException(403, "Die Freigabe „IServ-Gruppenordner“ ist aus. Einschalten auf der Freigaben-Seite.")
    gruppenordnerablage.starten()
    return {"ok": True}


@app.get("/api/gruppenordner/suche")
def gruppenordner_suche(q: str) -> dict:
    if not konnektoren.benutzbar(gruppenordnerablage.KONNEKTOR):
        raise HTTPException(403, "Die Freigabe „IServ-Gruppenordner“ ist aus. Einschalten auf der Freigaben-Seite.")
    return {"treffer": gruppenordnerablage.standard().suchen(q[:200], 20)}


_INLINE_TYPEN = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "webp": "image/webp", "gif": "image/gif", "txt": "text/plain; charset=utf-8"}


@app.get("/api/gruppenordner/{gid}/original")
def gruppenordner_original(gid: int):
    e = gruppenordnerablage.standard().eintrag(gid)
    if not e:
        raise HTTPException(404, "Diese Datei gibt es nicht (mehr).")
    endung = e["dateiname"].rsplit(".", 1)[-1].lower() if "." in e["dateiname"] else "bin"
    endung = re.sub(r"[^a-z0-9]", "", endung)[:10] or "bin"
    pfad = gruppenordnerablage.standard().original_pfad(gid, endung)
    if not pfad.exists():
        raise HTTPException(404, "Die Datei liegt lokal nicht (mehr) vor.")
    # Die Datei stammt aus fremder Hand (Lehrer, Mitschüler). Als HTML oder SVG inline
    # ausgeliefert, liefe ihr Skript in Fridays Herkunft — also nur harmlose Typen
    # inline, alles andere als Download.
    typ = _INLINE_TYPEN.get(endung)
    return Response(pfad.read_bytes(), media_type=typ or "application/octet-stream", headers={
        "Content-Disposition": f"{'inline' if typ else 'attachment'}; filename*=UTF-8''{quote(e['dateiname'])}"})


# Schulbücher: hochladen (mehrere PDFs je Buch), einmal durchlesen, Seiten ansehen.

def _buch_oder_404(bid: int) -> dict:
    b = buecherablage.standard().buch(bid)
    if not b:
        raise HTTPException(404, "Dieses Buch gibt es nicht.")
    return b


@app.get("/api/buecher")
def buecher_liste() -> dict:
    a = buecherablage.standard()
    return {"buecher": [buecherablage.uebersicht(b) for b in a.alle()],
            "entfernt": [buecherablage.uebersicht(b) for b in a.alle(entfernte=True)],
            "faecher": sorted(set(hausaufgabenablage.FAECHER.values())),
            "frei": konnektoren.benutzbar(buecherablage.KONNEKTOR)}


async def _pdfs_annehmen(dateien: list[UploadFile]) -> list[tuple[str, Path]]:
    """Erst alle Dateien prüfen (in einem Eingangsordner), dann gehört etwas zu einem Buch —
    sonst bliebe nach einer abgelehnten Datei ein leeres Buch stehen."""
    if not dateien:
        raise HTTPException(400, "Bitte mindestens eine PDF-Datei wählen.")
    eingang = buecherablage.standard().ordner / "schule" / "buecher" / "eingang" / os.urandom(8).hex()
    eingang.mkdir(parents=True, exist_ok=True)
    angenommen = []
    try:
        for nummer, datei in enumerate(dateien, start=1):
            name = datei.filename or f"scan-{nummer}.pdf"
            ziel = eingang / buecherablage.dateiname(nummer, name)
            erstes = await datei.read(1024 * 1024)
            if not erstes.startswith(b"%PDF-"):
                raise HTTPException(400, f"„{name}“ ist keine PDF-Datei.")
            groesse = 0
            with open(ziel, "wb") as f:
                stueck = erstes
                while stueck:
                    groesse += len(stueck)
                    if groesse > buecherablage.MAX_DATEI:
                        raise HTTPException(413, f"„{name}“ ist größer als 300 MB.")
                    f.write(stueck)
                    stueck = await datei.read(1024 * 1024)
            try:
                await asyncio.to_thread(buecherablage.seitenzahl, ziel)
            except Exception:
                raise HTTPException(400, f"„{name}“ ließ sich nicht öffnen.") from None
            angenommen.append((name, ziel))
        return angenommen
    except Exception:
        shutil.rmtree(eingang, ignore_errors=True)
        raise


def _pdfs_uebernehmen(bid: int, angenommen: list[tuple[str, Path]]) -> None:
    ordner = buecherablage.standard().buch_ordner(bid)
    ordner.mkdir(parents=True, exist_ok=True)
    start = len(list(ordner.glob("*.pdf")))
    verschoben = []
    eltern_ordner = set()
    for nummer, (name, pfad) in enumerate(sorted(angenommen, key=lambda x: buecherablage.natuerlich(x[0])),
                                          start=start + 1):
        ziel = ordner / buecherablage.dateiname(nummer, name)
        eltern_ordner.add(pfad.parent)
        pfad.replace(ziel)
        verschoben.append((name, ziel))
    for eo in eltern_ordner:
        shutil.rmtree(eo, ignore_errors=True)
    buecherablage.dateien_hinzufuegen(bid, verschoben)


@app.post("/api/buecher")
async def buch_anlegen(dateien: list[UploadFile], titel: str = Form(...), fach: str = Form(...)) -> dict:
    titel = " ".join(titel.split())[:120]
    if not titel or fach not in set(hausaufgabenablage.FAECHER.values()):
        raise HTTPException(400, "Bitte Titel und Fach angeben.")
    angenommen = await _pdfs_annehmen(dateien)
    bid = buecherablage.standard().anlegen(titel, fach)
    _pdfs_uebernehmen(bid, angenommen)
    gestartet = buecherablage.index_starten(bid)
    return {"ok": True, "id": bid, "gestartet": gestartet}


@app.post("/api/buecher/{bid}/dateien")
async def buch_ergaenzen(bid: int, dateien: list[UploadFile]) -> dict:
    _buch_oder_404(bid)
    _pdfs_uebernehmen(bid, await _pdfs_annehmen(dateien))
    return {"ok": True, "gestartet": buecherablage.index_starten(bid)}


@app.post("/api/buecher/{bid}/lesen")
async def buch_lesen(bid: int) -> dict:
    _buch_oder_404(bid)
    if not buecherablage.index_starten(bid):
        raise HTTPException(403, "Die Freigabe „Hausaufgaben bearbeiten“ ist aus. Einschalten auf der Freigaben-Seite.")
    return {"ok": True}


@app.post("/api/buecher/{bid}/entfernen")
def buch_entfernen(bid: int) -> dict:
    _buch_oder_404(bid)
    if buecherablage.laeuft(bid):
        raise HTTPException(409, "Friday liest das Buch gerade — bitte warten, bis sie fertig ist.")
    buecherablage.entfernen(bid)
    return {"ok": True}


@app.post("/api/buecher/{bid}/wiederherstellen")
def buch_wiederherstellen(bid: int) -> dict:
    _buch_oder_404(bid)
    buecherablage.wiederherstellen(bid)
    return {"ok": True}


@app.post("/api/buecher/{bid}/loeschen")
def buch_loeschen(bid: int) -> dict:
    b = _buch_oder_404(bid)
    if not b.get("entfernt"):
        raise HTTPException(409, "Erst entfernen, dann endgültig löschen.")
    if buecherablage.laeuft(bid):
        raise HTTPException(409, "Friday liest das Buch gerade — bitte warten, bis sie fertig ist.")
    buecherablage.loeschen(bid)
    return {"ok": True}


@app.get("/api/buecher/{bid}/seite/{seite}")
async def buch_seite(bid: int, seite: int):
    _buch_oder_404(bid)
    treffer = await asyncio.to_thread(buecherablage.seite_bild, bid, seite)
    if not treffer:
        raise HTTPException(404, f"Seite {seite} ist nicht eingescannt.")
    return Response(treffer[0], media_type="image/jpeg")


def _profil_pruefen(profil: int, neu_erlaubt: bool = True) -> None:
    """Gibt es dieses Profil, und darf man daran noch etwas anlegen (nicht archiviert)?"""
    if not profil:
        return
    p = garantieablage.standard().profil(profil)
    if not p:
        raise HTTPException(404, "Dieses Profil gibt es nicht (mehr).")
    if not neu_erlaubt and p.get("archiviert"):
        raise HTTPException(409, f"{p.get('name')} ist archiviert — hol das Profil zuerst zurück.")


GEMERKT_STUNDEN = 48      # Eike, 21.09.2026: ältere Fahrten machen die Liste nur voll (gespeichert bleiben sie für Anträge)


def _garantie_reisen() -> list[dict]:
    """Die gemerkten verspäteten Fahrten der letzten 48 Stunden — für alle Personen dieselben."""
    grenze = config.jetzt() - timedelta(hours=GEMERKT_STUNDEN)
    return [garantiesuche.oeffentlich(r) for r in sorted(
        (r for r in garantieablage.standard().reisen(mindest=garantiesuche.GARANTIE_AB, tage=3)
         if garantiesuche.beantragbar(r["kategorie"], r["linie"]) and _plan_ab(r.get("plan")) >= grenze),
        key=garantiesuche.relevanz)][:100]


def _plan_ab(iso) -> datetime:
    try:
        zeit = datetime.fromisoformat(str(iso))
        return zeit if zeit.tzinfo else zeit.replace(tzinfo=config.jetzt().tzinfo)
    except ValueError:
        return datetime.min.replace(tzinfo=config.jetzt().tzinfo)


def _email_basis() -> str:
    """Für den Alias-Vorschlag (name+person@…): nur die Adresse, nie das Passwort."""
    gmail_zugang = konnektoren.zugang("gmail")
    return gmail_zugang[0] if gmail_zugang and "@" in gmail_zugang[0] else ""


def _garantie_verwaltung() -> list[dict]:
    """Eine Störung der Verfolgung darf die Garantie-Seite nie leer lassen."""
    try:
        return garantieverwaltung.offene()
    except Exception:                                          # noqa: BLE001
        log.exception("Garantie-Verwaltung nicht lesbar")
        return []


@app.get("/api/garantie/uebersicht")
def garantie_uebersicht() -> dict:
    """Alle Personen auf einen Blick (wer noch kann, wer voll ist, was fehlt), was über alle hinweg ansteht, und die
    Fahrten. Ohne IBAN und Adressen (kern/garantie/uebersicht.py)."""
    return {"aktiv": konnektoren.benutzbar(GARANTIE), "abschicken": konnektoren.schreiben_erlaubt(GARANTIE),
            "orte": list(garantiesuche.ORTE), "email_basis": _email_basis(), "post": garantiepost.benutzbar(),
            "personen": garantieuebersicht.alle(), "aufgaben": garantieuebersicht.aufgaben(),
            # Über alle Personen zusammen (anders als `/api/garantie?profil=…`, das ist je eine Person).
            "statistik_gesamt": garantiepost.statistik(None),
            # Offene Vorgänge mit Phase, nächstem Schritt und Frist (kern/garantie/verwaltung.py).
            "verwaltung": _garantie_verwaltung(),
            "reisen": _garantie_reisen(), "bahn": fahrgastrechte.stand(None)}


@app.get("/api/garantie/fahrt/personen")
def garantie_fahrt_personen(id: str) -> dict:
    """Für wen sich diese Fahrt vorbereiten lässt — mit Grund, wenn nicht (Limit, Frist, schon beantragt)."""
    try:
        personen = garantieuebersicht.fuer_fahrt(id[:20])
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    # Garantie ODER Fahrgastrechte (Bedingungen der ÜSTRA): Wer die Fahrt schon als Bahn-Fall hat, kann sie hier nicht wählen.
    for p in personen:
        if fall := bahnablage.fall_zur_reise(id[:20], p["id"]):
            p["geht"] = False
            p["gruende"].insert(0, f"schon Bahn-Fahrgastrecht (Fall #{fall['id']})")
    return {"personen": personen}


@app.get("/api/garantie")
def garantie_stand(profil: int = 0) -> dict:
    """Alles für EINE Person (Vorgabe: Eike). Fahrten sind für alle gleich; Anträge, Angaben, Ticket,
    Höchstzahlen, Bescheide und die Abrechnung gehören zum Profil."""
    _profil_pruefen(profil)
    a = garantieablage.standard()
    return {"profil": profil, "profile": [garantieprofile.oeffentlich(p) for p in a.profile()],
            "email_basis": _email_basis(),
            "abrechnung": garantieprofile.abrechnung(profil) if profil else None,
            "aktiv": konnektoren.benutzbar(GARANTIE), "orte": list(garantiesuche.ORTE),
            "abschicken": konnektoren.schreiben_erlaubt(GARANTIE),
            "reisen": _garantie_reisen(),
            "antraege": [garantieantrag.bewerten(e) for e in a.antraege(50, profil)],
            "zaehler": garantieantrag.zaehler(profil),
            # Nie die IBAN — nur verdeckt (angaben_sicher).
            "angaben": a.angaben_sicher(profil), "ticket": a.ticket(profil), "tickets": a.tickets(profil)[:6],
            "fahrkarten": garantiedienst.FAHRKARTEN,
            "post": garantiepost.benutzbar(), "statistik": garantiepost.statistik(profil),
            "vorgaenge": [garantiepost.oeffentlich(v) for v in a.vorgaenge(200, profil)],
            "bahn": fahrgastrechte.stand(profil)}


# ---------- Profile: Freunde, für die Eike einreicht ----------

@app.post("/api/garantie/profile")
def garantie_profil_anlegen(d: GarantieProfil) -> dict:
    try:
        daten = garantieprofile.pruefen(d.model_dump(exclude_unset=True), neu=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    a = garantieablage.standard()
    pid = a.profil_anlegen(daten)
    return {"ok": True, "id": pid, "angaben": a.angaben_sicher(pid)}


@app.post("/api/garantie/profile/aus-ticket")
async def garantie_profil_aus_ticket(dateien: list[UploadFile], name: str = Form("")) -> dict:
    """Eine neue Person anlegen: Eike gibt nur einen Namen (für sich) und lädt ihre Tickets hoch. Friday liest aus jedem Ticket
    Art, Gültigkeit, Namen und Nummern, legt das Profil damit an und speichert die Tickets dort — Eike ergänzt danach nur noch,
    was fehlt (Adresse, E-Mail, Anteil). Gibt es die Person schon (gleicher Name auf dem Ticket), wird nichts angelegt."""
    if not dateien:
        raise HTTPException(400, "Bitte mindestens ein Ticket wählen.")
    if len(dateien) > 6:
        raise HTTPException(400, "Höchstens 6 Tickets auf einmal.")
    roh = []
    for datei in dateien:
        mime = (datei.content_type or "").lower()
        if mime not in TICKET_TYPEN:
            raise HTTPException(400, f"„{datei.filename or 'Datei'}“: bitte eine PDF oder ein Foto (JPEG, PNG, WebP, HEIC).")
        inhalt = await datei.read(MAX_TICKET + 1)
        if not inhalt:
            raise HTTPException(400, f"„{datei.filename or 'Datei'}“ ist leer.")
        if len(inhalt) > MAX_TICKET:
            raise HTTPException(400, f"„{datei.filename or 'Datei'}“: höchstens 10 MB.")
        try:
            erkannt = await garantieticket.auslesen(mime, inhalt)
        except Exception as exc:
            erkannt = {}
            roh.append((datei, mime, inhalt, erkannt, f"„{datei.filename or 'Ticket'}“ konnte Friday gerade nicht lesen "
                                                     f"({type(exc).__name__}) — trag die Angaben selbst ein."))
            continue
        roh.append((datei, mime, inhalt, erkannt, ""))
    a = garantieablage.standard()
    # Wem gehört das? Der erste Name, den ein Ticket verrät.
    inhaber = next((e["inhaber"] for _, _, _, e, _ in roh if e.get("inhaber")), "")
    teile = inhaber.split()
    vorname, nachname = (" ".join(teile[:-1]), teile[-1]) if len(teile) > 1 else ("", teile[0] if teile else "")
    if nachname:
        for p in a.profile():
            if (p.get("nachname") or "").casefold() == nachname.casefold() and (p.get("vorname") or "").casefold() == vorname.casefold():
                return {"ok": False, "vorhanden": {"id": p["id"], "name": p.get("name") or f"Profil {p['id']}"},
                        "text": f"{p.get('name')} gibt es schon (auf dem Ticket steht {inhaber}) — die Tickets lassen sich dort hinzufügen."}
    label = " ".join(name.split())[:60] or vorname or nachname or "Neue Person"
    pid = a.profil_anlegen({"name": label, "vorname": vorname, "nachname": nachname})
    texte, infos = [], []
    for datei, mime, inhalt, erkannt, fehler in roh:
        info = a.ticket_speichern(datei.filename or "ticket", mime, inhalt, erkannt, pid)
        infos.append(info)
        if erkannt:
            t = garantieuebernahme.uebernehmen(a, info)
            texte.append(("Das sieht nicht nach einer Fahrkarte aus — bitte prüfen. " if not erkannt["ist_fahrkarte"] else "") + t)
        else:
            texte.append(fehler)
    return {"ok": True, "id": pid, "tickets": infos, "text": " ".join(texte), "angaben": a.angaben_sicher(pid)}


@app.put("/api/garantie/profile/{pid}")
def garantie_profil_aendern(pid: int, d: GarantieProfil) -> dict:
    _profil_pruefen(pid)
    try:
        daten = garantieprofile.pruefen(d.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    a = garantieablage.standard()
    a.profil_aendern(pid, daten)
    return {"ok": True, "angaben": a.angaben_sicher(pid)}


@app.delete("/api/garantie/profile/{pid}")
def garantie_profil_loeschen(pid: int) -> dict:
    """Nur ein Profil, an dem nichts hängt. Sonst archivieren — Anträge und Tickets bleiben als Beleg."""
    _profil_pruefen(pid)
    if not garantieablage.standard().profil_loeschen(pid):
        raise HTTPException(409, "An diesem Profil hängen Anträge — sie zählen für die Höchstzahlen und die Abrechnung "
                                 "und bleiben als Beleg. Archiviere das Profil stattdessen.")
    return {"ok": True}


@app.post("/api/garantie/profile/{pid}/erledigt")
def garantie_profil_erledigt(pid: int, e: GarantieErledigt) -> dict:
    """Eikes Buchführung: Zu diesem Fall ist mit dem Freund abgerechnet (oder wieder nicht)."""
    _profil_pruefen(pid)
    garantieablage.standard().ausgezahlt_setzen(pid, e.vorgang, e.erledigt)
    return {"ok": True, "abrechnung": garantieprofile.abrechnung(pid)}


class VorgangErledigt(BaseModel):
    erledigt: bool


@app.post("/api/garantie/vorgang/{nummer}/erledigt")
def garantie_vorgang_erledigt(nummer: str, e: VorgangErledigt) -> dict:
    """Eike hakt eine alte Rückfrage der ÜSTRA ab (oder holt sie zurück). Gilt nur bis zur nächsten Mail zu dem Fall."""
    a = garantieablage.standard()
    v = a.vorgang(nummer[:12])
    if not v:
        raise HTTPException(404, "Diesen Fall gibt es nicht.")
    if e.erledigt and v.get("status") != "rueckfrage":
        raise HTTPException(409, "Abhaken geht nur bei einer Rückfrage.")
    a.vorgang_setzen(v["vorgang"], erledigt=v.get("letzte_mail") or "?" if e.erledigt else None)
    return {"ok": True, "stand": garantiepost.anzeige_stand(a.vorgang(v["vorgang"]))}


@app.post("/api/garantie/post")
async def garantie_post_abgleichen() -> dict:
    """Jetzt in Gmail nach Bescheiden der ÜSTRA sehen (sonst stündlich)."""
    if not garantiepost.benutzbar():
        raise HTTPException(409, "Gmail ist noch nicht verbunden (Freigaben → Gmail, App-Passwort).")
    try:
        ergebnis = await garantiepost.abgleich()
    except gmail.AnmeldeFehler as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        raise HTTPException(502, f"Gmail antwortet gerade nicht ({type(exc).__name__}).") from None
    return {"ok": True, **ergebnis}


@app.post("/api/garantie/angaben")
def garantie_angaben(e: GarantieAngaben) -> dict:
    daten = e.model_dump(exclude_none=True)
    if daten.get("iban") and not garantieablage.iban_gueltig(daten["iban"]):
        raise HTTPException(400, "Diese IBAN stimmt nicht — die Prüfsumme passt nicht. Bitte noch einmal prüfen.")
    if daten.get("fahrkarte") and daten["fahrkarte"] not in garantiedienst.FAHRKARTEN:
        raise HTTPException(400, "Diese Fahrkartenart kennt das Formular nicht.")
    if daten.get("gueltig_bis"):
        try:
            datetime.fromisoformat(daten["gueltig_bis"])
        except ValueError:
            raise HTTPException(400, "„Gültig bis“ bitte als Datum.") from None
    a = garantieablage.standard()
    a.angaben_setzen(daten)
    return {"ok": True, "angaben": a.angaben_sicher()}


@app.post("/api/garantie/ticket")
async def garantie_ticket(datei: UploadFile, profil: int = 0) -> dict:
    _profil_pruefen(profil, neu_erlaubt=False)
    mime = (datei.content_type or "").lower()
    if mime not in TICKET_TYPEN:
        raise HTTPException(400, "Bitte eine PDF oder ein Foto (JPEG, PNG, WebP, HEIC).")
    inhalt = await datei.read(MAX_TICKET + 1)
    if not inhalt:
        raise HTTPException(400, "Die Datei ist leer.")
    if len(inhalt) > MAX_TICKET:
        raise HTTPException(400, "Höchstens 10 MB.")
    # Friday liest Art und Gültigkeit selbst aus (Eike: „die Daten werden daraus übernommen“).
    erkannt, text = {}, ""
    try:
        erkannt = await garantieticket.auslesen(mime, inhalt)
    except Exception as exc:
        text = (f"Friday konnte das Ticket gerade nicht lesen ({type(exc).__name__}) — "
                "trag Art und Gültigkeit bitte selbst ein.")
    a = garantieablage.standard()
    info = a.ticket_speichern(datei.filename or "ticket", mime, inhalt, erkannt, profil)
    if erkannt:
        text = garantieuebernahme.uebernehmen(a, info)
        if not erkannt["ist_fahrkarte"]:
            text = "Das sieht nicht nach einer Fahrkarte aus — bitte prüfen. " + text
    return {"ok": True, "ticket": info, "text": text}


class TicketKorrektur(BaseModel):
    art: Optional[str] = Field(None, max_length=40)
    gueltig_von: Optional[str] = Field(None, max_length=10)
    gueltig_bis: Optional[str] = Field(None, max_length=10)
    kundenkarte: Optional[str] = Field(None, max_length=6, pattern=r"^\d*$")      # das Formular verlangt genau 6 Ziffern
    ticketnummer: Optional[str] = Field(None, max_length=40)     # auf Papier mit Trennzeichen („36-12.345.678“)
    preis: Optional[float] = Field(None, ge=0, le=500)
    inhaber: Optional[str] = Field(None, max_length=80)
    form: Optional[Literal["digital", "papier", ""]] = None
    nummern_geprueft: Optional[bool] = None                     # Eike hat Kundenkarten- und Ticketnummer mit dem Original verglichen


@app.post("/api/garantie/ticket/{tid}")
def garantie_ticket_korrigieren(tid: str, k: TicketKorrektur) -> dict:
    """Falls Friday etwas falsch gelesen hat."""
    daten = k.model_dump(exclude_none=True)
    if daten.get("art") and daten["art"] not in garantiedienst.FAHRKARTEN:
        raise HTTPException(400, "Diese Fahrkartenart kennt das Formular nicht.")
    for feld in ("gueltig_von", "gueltig_bis"):
        if daten.get(feld):
            try:
                datetime.fromisoformat(daten[feld])
            except ValueError:
                raise HTTPException(400, "Datum bitte als JJJJ-MM-TT.") from None
    if daten.get("kundenkarte") and not garantieticket.KUNDENKARTE.match(daten["kundenkarte"]):
        raise HTTPException(422, "Die Kundenkartennummer hat genau 6 Ziffern.")
    daten["ticketnummer"] = garantieticket.nummer(daten.get("ticketnummer", "")) if "ticketnummer" in daten else None
    if daten.get("ticketnummer") and not garantieticket.TICKETNUMMER.match(daten["ticketnummer"]):
        raise HTTPException(422, "Die Ticketnummer besteht nur aus Buchstaben und Ziffern (in der ÜSTRA-App steht sie oben, "
                                 "hinter dem Datum).")
    daten = {k: v for k, v in daten.items() if v is not None}
    a = garantieablage.standard()
    try:
        t = a.ticket_korrigieren(tid[:20], daten)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    if a.ticket(t.get("profil", 0)) and a.ticket(t.get("profil", 0))["id"] == t["id"]:
        garantieuebernahme.uebernehmen(a, t)
    return {"ok": True, "ticket": t}


@app.get("/api/garantie/ticket/{tid}/datei")
def garantie_ticket_datei(tid: str):
    """Das hochgeladene Ticket selbst — zum Vergleichen der Nummern mit dem, was Friday gelesen hat."""
    a = garantieablage.standard()
    t = next((x for x in a._alle_tickets() if x.get("id") == tid[:20]), None)
    pfad = a.ordner / "garantie" / "tickets" / str((t or {}).get("datei") or "")
    if not t or not t.get("datei") or not pfad.is_file():
        raise HTTPException(404, "Diese Ticketdatei gibt es nicht.")
    return Response(pfad.read_bytes(), media_type=t.get("mime") or "application/octet-stream",
                    headers={"X-Content-Type-Options": "nosniff"})              # no-store setzt frisch_halten


@app.post("/api/garantie/suchen")
async def garantie_suchen() -> dict:
    """Der Suchen-Knopf: jetzt in Eikes Gegend nach verspäteten Stadtbahnen und Bussen sehen."""
    _garantie_an()
    return await garantiesuche.suchen()


@app.get("/api/garantie/fahrt")
async def garantie_fahrt(id: str) -> dict:
    """Der Verlauf einer gefundenen Fahrt mit allen Halten und ihrer Verspätung."""
    _garantie_an()
    try:
        return garantieantrag.verlauf_oeffentlich(await garantieantrag.verlauf(id[:20]))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except garantieantrag.gvh.GvhFehler as exc:
        raise HTTPException(502, str(exc)) from None


@app.post("/api/garantie/vorbereiten")
async def garantie_vorbereiten(v: GarantieVorbereiten) -> dict:
    _garantie_an()
    _profil_pruefen(v.profil, neu_erlaubt=False)
    return await garantieantrag.vorbereiten(v.fahrt, v.start, v.ziel, v.profil)


@app.post("/api/garantie/antrag/{aid}")
def garantie_antrag_status(aid: int, s: GarantieStatus) -> dict:
    """Nur Eike markiert: abgeschickt (auf der Webseite eingereicht), verworfen, wieder offen."""
    try:
        return {"ok": True, "antrag": garantieantrag.status_setzen(aid, s.status)}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


class GarantieAbschicken(BaseModel):
    bestaetigt: bool = False
    profil: int = 0            # für wen die App den Antrag gezeigt hat — muss zum Antrag passen


@app.get("/api/garantie/antrag/{aid}/pruefen")
async def garantie_antrag_pruefen(aid: int) -> dict:
    """Was beim Abschicken gesendet würde — ohne zu senden."""
    p = await garantieabschicken.pruefen(aid)
    return {"ok": p["ok"], "probleme": p["probleme"], "punkte": p["punkte"]}


@app.post("/api/garantie/antrag/{aid}/abschicken")
async def garantie_antrag_abschicken(aid: int, b: GarantieAbschicken) -> dict:
    """Nur Eike, nur mit Bestätigung: den Antrag an die ÜSTRA senden."""
    if not b.bestaetigt:
        raise HTTPException(400, "Abschicken braucht deine Bestätigung.")
    if not konnektoren.schreiben_erlaubt(GARANTIE):
        raise HTTPException(409, "„Anträge abschicken“ ist in den Freigaben noch aus.")
    e = garantieablage.standard().antrag(aid)
    if e and int(e.get("profil") or 0) != b.profil:
        raise HTTPException(409, "Dieser Antrag gehört zu einer anderen Person, als die Seite gerade zeigt — "
                                 "bitte die Seite neu laden.")
    return await garantieabschicken.abschicken(aid)


@app.get("/api/garantie/antrag/{aid}/abtippen")
def garantie_antrag_abtippen(aid: int) -> dict:
    """Alle Angaben in der Reihenfolge des Formulars — zum Abtippen, wenn Eike selbst einreicht."""
    d = garantieabschicken.abtippen(aid)
    if not d:
        raise HTTPException(404, "Diesen Antrag gibt es nicht.")
    return d


@app.delete("/api/garantie/antrag/{aid}")
def garantie_antrag_loeschen(aid: int) -> dict:
    """Eike löscht einen Entwurf. Abgeschickte bleiben — sie zählen für die Grenzen."""
    if not garantieablage.standard().antrag_loeschen(aid):
        raise HTTPException(409, "Löschen geht nur bei Entwürfen — abgeschickte Anträge bleiben für die Zähler.")
    return {"ok": True}


class VerwaltungPhase(BaseModel):
    phase: str = Field(max_length=40)
    grund: str = Field("", max_length=200)
    bestaetigt: bool = False
    betrag: Optional[float] = Field(None, ge=0, le=1000)      # nur Bahn: erhaltener Betrag bei „Genehmigt“


@app.post("/api/garantie/verwaltung/{ref}/phase")
def garantie_verwaltung_phase(ref: str, b: VerwaltungPhase) -> dict:
    """Eike setzt die Phase eines Vorgangs (Knöpfe in der Übersicht). Nur mit Bestätigung, Übergangsprüfung wie beim
    Werkzeug (`verwaltung.setzen`). „abgeschickt“ gibt es hier nie — das bleibt dem Abschicken-Knopf. Sendet und mailt nichts."""
    _garantie_an()
    if not b.bestaetigt:
        raise HTTPException(400, "Das braucht deine Bestätigung.")
    if b.phase not in garantieverwaltung.PHASEN:
        raise HTTPException(400, f"„{b.phase}“ ist kein bekannter Status.")
    if b.phase == "abgeschickt":
        raise HTTPException(400, "„Abgeschickt“ setzt nur der Knopf „Abschicken“ (mit Prüfung) — hier nicht.")
    try:
        ref = garantieverwaltung.aufloesen(ref)
        neu = garantieverwaltung.setzen(ref, b.phase, (b.grund or "Eike in der App")[:200], quelle="eike",
                                        betrag_cent=round(b.betrag * 100) if b.betrag is not None else None)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"ok": True, "ref": ref, "phase": neu, "phase_text": garantieverwaltung.PHASE_TEXT[neu],
            "verwaltung": _garantie_verwaltung()}


class VerwaltungAlle(BaseModel):
    bestaetigt: bool = False
    grund: str = Field("", max_length=200)


@app.post("/api/garantie/verwaltung/alle-abschliessen")
def garantie_verwaltung_alle_abschliessen(b: VerwaltungAlle) -> dict:
    """Knopf „Alle abhaken“ im Block Verfolgung: beendet alle offenen Vorgänge (Historie bleibt). Sendet und mailt nichts."""
    _garantie_an()
    if not b.bestaetigt:
        raise HTTPException(400, "Das braucht deine Bestätigung.")
    z = garantieverwaltung.alle_abschliessen(b.grund)
    return {"ok": True, **z, "verwaltung": _garantie_verwaltung()}


# ---------- Bahn-Fahrgastrechte ----------
# Züge (S-Bahn bis ICE), gemessen wie in der Garantie, gerechnet nach Tarif (kern/garantie/fahrgastrechte.py). Eigene
# Freigabe, standardmäßig aus. Abgeschickt wird NICHTS: PDF zum Drucken, Liste zum Abtippen, „eingereicht“ setzt Eike.

def _bahn_an() -> None:
    if not fahrgastrechte.benutzbar():
        raise HTTPException(409, "Die Freigabe „Bahn-Fahrgastrechte“ ist aus — einschalten auf der Freigaben-Seite.")


class BahnFall(BaseModel):
    fahrt: str = Field(min_length=6, max_length=20)
    start: str = Field(min_length=3, max_length=60)      # Kennung des Halts aus dem Verlauf
    ziel: str = Field(min_length=3, max_length=60)
    profil: int = Field(0, ge=0)
    fahrkarte: Literal["zeitkarte", "einzel"] = "zeitkarte"
    preis: Optional[float] = Field(None, gt=0, le=1000)   # Euro, nur bei der Einzelfahrkarte (für diese Richtung)
    auftrag: Optional[str] = Field(None, max_length=20, pattern=r"^[A-Za-z0-9]*$")
    rueckfahrt: bool = False


class BahnFallStatus(BaseModel):
    status: Literal["verworfen", "offen"]


class BahnEingereicht(BaseModel):
    bestaetigt: bool = False


class BahnBescheid(BaseModel):
    status: Literal["eingereicht", "genehmigt", "abgelehnt"]
    betrag: Optional[float] = Field(None, ge=0, le=1000)
    grund: str = Field("", max_length=300)
    vorgang: str = Field("", max_length=40)


@app.get("/api/garantie/bahn")
def bahn_stand(profil: Optional[int] = None) -> dict:
    """Fälle, Anträge und Regeln — einer Person oder (ohne `profil`) aller. Keine IBAN, keine Adresse."""
    if profil is not None:
        _profil_pruefen(profil)
    return fahrgastrechte.stand(profil)


@app.get("/api/garantie/bahn/zuege")
async def bahn_zuege(halt: str, zeit: str) -> dict:
    """Züge an einem Bahnhof um eine Zeit — auch ICE und IC, die der Sammler nicht sucht."""
    _bahn_an()
    try:
        wann = datetime.fromisoformat(zeit[:16])
    except ValueError:
        raise HTTPException(400, "Die Zeit verstehe ich nicht.") from None
    wann = wann if wann.tzinfo else wann.replace(tzinfo=config.jetzt().tzinfo)
    if not (config.jetzt() - timedelta(days=3) <= wann <= config.jetzt() + timedelta(days=1)):
        raise HTTPException(400, "Die Auskunft kennt Züge nur von den letzten Tagen bis morgen — ältere Fahrten haben "
                                 "keine Echtzeit mehr.")
    halt = " ".join((halt or "").split())[:60]
    if len(halt) < 3:
        raise HTTPException(400, "Welcher Bahnhof?")
    try:
        return await fahrgastrechte.zuege_am(halt, wann)
    except garantieantrag.gvh.GvhFehler as exc:
        raise HTTPException(404, str(exc)) from None


@app.get("/api/garantie/bahn/verlauf")
async def bahn_verlauf(id: str) -> dict:
    """Der Verlauf eines Zugs mit allen Halten — zum Antippen von Start und Ziel."""
    _bahn_an()
    try:
        v = garantieantrag.verlauf_oeffentlich(await garantieantrag.verlauf(id[:20]))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except garantieantrag.gvh.GvhFehler as exc:
        raise HTTPException(502, str(exc)) from None
    return {**v, "zug": fahrgastrechte.ist_bahn(v.get("kategorie") or "", v.get("linie") or ""),
            "fern": fahrgastrechte.ist_fernverkehr(v.get("kategorie") or "", v.get("linie") or ""),
            "stelle": fahrgastrechte.STELLEN[fahrgastrechte.stelle_fuer(v.get("betreiber") or "", v.get("linie") or "")]["name"]}


@app.post("/api/garantie/bahn/fall")
async def bahn_fall(f: BahnFall) -> dict:
    _bahn_an()
    _profil_pruefen(f.profil, neu_erlaubt=False)
    return await fahrgastrechte.erfassen(f.fahrt, f.start, f.ziel, f.profil, f.fahrkarte,
                                         round(f.preis * 100) if f.preis else None, f.auftrag or "", f.rueckfahrt)


@app.post("/api/garantie/bahn/fall/{fid}")
def bahn_fall_status(fid: int, s: BahnFallStatus) -> dict:
    """Verwerfen oder zurückholen — nur, was noch nicht eingereicht ist."""
    f = bahnablage.fall(fid)
    if not f:
        raise HTTPException(404, "Diesen Fall gibt es nicht.")
    if f.get("antrag"):
        raise HTTPException(409, "Der Fall ist schon eingereicht.")
    bahnablage.fall_aendern(fid, status=s.status)
    return {"ok": True, "fall": fahrgastrechte.fall_oeffentlich(fahrgastrechte.bewerten(bahnablage.fall(fid)))}


@app.delete("/api/garantie/bahn/fall/{fid}")
def bahn_fall_loeschen(fid: int) -> dict:
    if not bahnablage.fall_loeschen(fid):
        raise HTTPException(409, "Löschen geht nur, solange der Fall nicht eingereicht ist.")
    return {"ok": True}


def _bahn_schluessel(schluessel: str) -> str:
    if not re.fullmatch(r"p\d+-(zk|ez)-[a-z0-9]+-[a-z]+", schluessel or ""):
        raise HTTPException(404, "Diesen Antrag gibt es nicht.")
    return schluessel


@app.get("/api/garantie/bahn/antrag/{schluessel}/pdf")
async def bahn_pdf(schluessel: str) -> Response:
    """Das ausgefüllte DB-Formular (je Fall) samt Anschreiben — ohne IBAN, ohne Unterschrift. Nur, wenn der Antrag bereit
    oder schon eingereicht ist."""
    _bahn_an()
    try:
        inhalt, name = await asyncio.to_thread(bahnformular.pdf, _bahn_schluessel(schluessel))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return Response(inhalt, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"', "Cache-Control": "no-store"})


@app.get("/api/garantie/bahn/antrag/{schluessel}/anschreiben")
async def bahn_anschreiben(schluessel: str) -> Response:
    _bahn_an()
    try:
        inhalt, name = await asyncio.to_thread(bahnformular.anschreiben_pdf, _bahn_schluessel(schluessel))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return Response(inhalt, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"', "Cache-Control": "no-store"})


@app.get("/api/garantie/bahn/antrag/{schluessel}/abtippen")
def bahn_abtippen(schluessel: str) -> dict:
    """Alles fürs Online-Formular der S-Bahn Hannover, in seiner Reihenfolge — die IBAN nur mit den Enden."""
    _bahn_an()
    try:
        return bahnformular.abtippen(_bahn_schluessel(schluessel))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/garantie/bahn/antrag/{schluessel}/eingereicht")
def bahn_eingereicht(schluessel: str, b: BahnEingereicht) -> dict:
    """Eike hat selbst eingereicht (Post, Reisezentrum, Webformular) — nur mit Bestätigung, nur wenn der Antrag bereit ist."""
    _bahn_an()
    if not b.bestaetigt:
        raise HTTPException(400, "Das braucht deine Bestätigung.")
    try:
        return {"ok": True, "antrag": fahrgastrechte.einreichen(_bahn_schluessel(schluessel))}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/garantie/bahn/eingereicht/{aid}")
def bahn_bescheid(aid: int, b: BahnBescheid) -> dict:
    """Was die Bahn geantwortet hat — genehmigt (mit Betrag) oder abgelehnt; setzt nur Eike."""
    try:
        return {"ok": True, "antrag": fahrgastrechte.bescheid(aid, b.status, round(b.betrag * 100) if b.betrag is not None
                                                                else None, b.grund, b.vorgang)}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


# ---------- Einstellungen: Benachrichtigungen ----------
# Eine Stelle für alles, was aufs Handy geht (kern/dienste/benachrichtigungen.py).
# Friday kann dieselben Schalter im Gespräch stellen (Werkzeug `benachrichtigung_stellen`).

class BenachrichtigungEinstellung(BaseModel):
    art: Optional[str] = Field(None, max_length=40)
    an: Optional[bool] = None
    ruhe_an: Optional[bool] = None
    ruhe_ab: Optional[str] = Field(None, max_length=8)
    ruhe_bis: Optional[str] = Field(None, max_length=8)


@app.get("/api/einstellungen/benachrichtigungen")
def benachrichtigungen_stand() -> dict:
    return benachrichtigungen.uebersicht()


@app.post("/api/einstellungen/benachrichtigungen")
def benachrichtigungen_stellen(e: BenachrichtigungEinstellung) -> dict:
    if e.art is not None:
        if e.an is None:
            raise HTTPException(400, "Zu einer Art gehört auch, ob sie an oder aus sein soll.")
        try:
            benachrichtigungen.stellen(e.art, e.an)
        except benachrichtigungen.UnbekannteArt as exc:
            raise HTTPException(400, str(exc)) from None
    if e.ruhe_an is not None or e.ruhe_ab is not None or e.ruhe_bis is not None:
        benachrichtigungen.ruhe_stellen(ein=e.ruhe_an, ab=e.ruhe_ab, bis=e.ruhe_bis)
    return benachrichtigungen.uebersicht()


# ---------- Einstellungen: Abfahrten im Blick ----------
# Eine Wache meldet, wenn Eikes Bus Verspätung hat. Sie hängt am GVH-Konnektor,
# weil sie dessen Echtzeit braucht.

class NeueWache(BaseModel):
    haltestelle: str = Field(..., max_length=120)
    linie: str = Field("", max_length=20)
    richtung: str = Field("", max_length=60)
    ab: str = Field("07:00", max_length=8)
    bis: str = Field("08:00", max_length=8)
    tage: list[int] = Field(default_factory=list)
    schwelle: int = Field(wache.SCHWELLE_MIN, ge=1, le=60)


class WacheStellen(BaseModel):
    aktiv: bool


def _wachen_frei() -> None:
    if not konnektoren.benutzbar("gvh"):
        raise HTTPException(403, "Der Fahrplan ist auf der Freigaben-Seite nicht eingeschaltet.")


@app.get("/api/wachen")
def wachen_liste() -> dict:
    return {"wachen": [{**w, "text": wache.text(w)} for w in wache.alle()],
            "art_an": benachrichtigungen.an(wache.ART)}


@app.post("/api/wachen")
async def wache_anlegen(w: NeueWache) -> dict:
    _wachen_frei()
    try:
        neu = await wache.anlegen(w.haltestelle, w.linie, w.richtung, w.ab, w.bis, w.tage, w.schwelle)
    except wache.WachenFehler as exc:
        raise HTTPException(400, str(exc)) from None
    return {**neu, "text": wache.text(neu)}


@app.post("/api/wachen/{kennung}")
def wache_stellen(kennung: str, s: WacheStellen) -> dict:
    geaendert = wache.stellen(kennung, s.aktiv)
    if geaendert is None:
        raise HTTPException(404, "Diese Wache gibt es nicht.")
    return {**geaendert, "text": wache.text(geaendert)}


class DashboardGesehen(BaseModel):
    id: str = Field(min_length=1, max_length=40)


@app.post("/api/minecraft")
async def minecraft_melden(request: Request) -> dict:
    """Die Mod `fridaycard` meldet den Spielstand. Nur mit dem Schlüssel aus .env (MINECRAFT_SCHLUESSEL)."""
    schluessel = os.getenv("MINECRAFT_SCHLUESSEL", "").strip()
    if not schluessel:
        raise HTTPException(503, "MINECRAFT_SCHLUESSEL fehlt in .env.")
    kopf = request.headers.get("authorization", "")
    if not (kopf.startswith("Bearer ") and hmac.compare_digest(kopf[7:].strip().encode(), schluessel.encode())):
        raise HTTPException(401, "Falscher Schlüssel.")
    try:
        minecraftkarte.annehmen(await request.json())
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True}


@app.get("/api/minecraft/skin")
async def minecraft_skin() -> Response:
    b = await minecraftkarte.skin_holen()
    if not b:
        raise HTTPException(404, "Kein Skin.")
    return Response(b, media_type="image/png", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"})


@app.get("/api/dashboard")
async def dashboard_karten(antwort: Response) -> dict:
    """Die Karten der Übersicht. Nie zwischenspeichern: Sie ändern sich im Sekundentakt."""
    antwort.headers["Cache-Control"] = "no-store"
    return await dashboardkarten.sammeln()


@app.post("/api/dashboard/gesehen")
def dashboard_gesehen(g: DashboardGesehen) -> dict:
    if not dashboardkarten.gesehen(g.id):
        raise HTTPException(404, "Diese Karte gibt es nicht (mehr).")
    return {"ok": True}


@app.post("/api/regeln/{rid}/loeschen")
def regel_loeschen_api(rid: int) -> dict:
    """Eikes Knopf „Löschen“ an einer Regel in der Karte (mit Rückfrage im Browser): Regel und ihre Läufe endgültig weg."""
    if not regelspeicher_api.loeschen(rid):
        raise HTTPException(404, "Diese Regel gibt es nicht (mehr).")
    return {"ok": True}


@app.post("/api/dashboard/musik/{befehl}")
async def dashboard_musik(befehl: str) -> dict:
    try:
        await dashboardmusik.befehl(befehl)
    except ValueError:
        raise HTTPException(404, "Diesen Befehl gibt es nicht.") from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except spotify.SpotifyFehler as exc:
        raise HTTPException(502, str(exc)) from None
    return {"ok": True}


class BildKarte(BaseModel):
    url: str = Field(max_length=120)
    titel: str = Field(min_length=1, max_length=120)
    quelle: str = Field("", max_length=80)
    lizenz: str = Field("", max_length=60)
    urheber: str = Field("", max_length=120)
    link: str = Field("", max_length=400)
    minuten: int = Field(friday_karten.MINUTEN_BILD, ge=1, le=friday_karten.MINUTEN_MAX)


@app.post("/api/dashboard/bild")
async def dashboard_bild(b: BildKarte, request: Request) -> dict:
    """„Auf die Übersicht“ unter einem Bild im Gespräch: legt eine Bildkarte — nur mit Fridays eigenen Bildern."""
    _gleiche_herkunft(request)
    try:
        karte = await friday_karten.legen_bild(b.url, b.titel, b.quelle, b.lizenz, b.urheber, b.link, b.minuten)
    except friday_karten.KarteFehler as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True, "id": karte["id"]}


class PersonNeu(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    kennung: str = Field("", max_length=24)


class PersonPause(BaseModel):
    pause: bool


class KontaktNeu(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    snapchat: str = Field("", max_length=200)
    persona: str = Field("", max_length=6000)
    werkzeuge: list[str] | None = Field(None, max_length=50)
    proaktiv: bool = False
    graph_name: str = Field("", max_length=200)


class FreigabeNeu(BaseModel):
    text: str = Field(max_length=6000)
    anlass: str = Field("", max_length=400)


class RueckfrageAntwort(BaseModel):
    text: str = Field("", max_length=6000)


class KontaktAenderung(BaseModel):
    name: str | None = Field(None, max_length=200)
    snapchat: str | None = Field(None, max_length=200)
    persona: str | None = Field(None, max_length=6000)
    werkzeuge: list[str] | None = Field(None, max_length=50)
    proaktiv: bool | None = None
    pausiert: bool | None = None
    senden_erlaubt: bool | None = None
    module: dict | None = None


class KontaktUnbekannt(BaseModel):
    kanal: Literal["snapchat"]
    anzeige: str = Field(max_length=200)


def _person_kennung(kennung: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,23}", kennung):
        raise HTTPException(404, "Diese Person gibt es nicht.")
    return kennung


def _einrichtung(d: dict) -> dict:
    """Was Eike für OwnTracks braucht — einmalig, mit Passwort. Wird nicht gespeichert."""
    return {"url": d.get("empfangs_url", ""), "benutzer": d.get("kennung", ""), "passwort": d.get("passwort", ""),
            "link": personendienst.owntracks_link(d), "name": d.get("name", "")}


@app.get("/api/personenstandort/personen")
async def personen_liste() -> dict:
    """Die Personen für die Verwaltung (ohne Koordinaten). Ein Fehler des Containers kommt als Text, nicht als Absturz."""
    try:
        return {"ok": True, "personen": await personendienst.verwaltungsliste()}
    except personendienst.PersonenFehler as exc:
        return {"ok": False, "text": str(exc), "personen": []}
    except Exception:
        return {"ok": False, "text": "Der Personenstandort-Container antwortet nicht (läuft er?).", "personen": []}


@app.post("/api/personenstandort/personen")
async def person_anlegen(p: PersonNeu, request: Request) -> dict:
    _gleiche_herkunft(request)
    name = p.name.strip()
    try:
        kennung = p.kennung.strip() or personendienst.kennung_aus_namen(name)
        if not name:
            raise ValueError("Der Name fehlt.")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,23}", kennung):
            raise ValueError("Die Kennung darf nur Kleinbuchstaben, Ziffern, - und _ enthalten (2–24 Zeichen, mit Buchstabe am Anfang).")
        d = await personendienst.anlegen(kennung, name)
    except ValueError as exc:
        return {"ok": False, "text": str(exc)}
    except personendienst.PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}
    return {"ok": True, "einrichtung": _einrichtung(d)}


@app.post("/api/personenstandort/personen/{kennung}/token")
async def person_neues_passwort(kennung: str, request: Request) -> dict:
    _gleiche_herkunft(request)
    try:
        d = await personendienst.neues_passwort(_person_kennung(kennung))
    except personendienst.PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}
    return {"ok": True, "einrichtung": _einrichtung(d)}


@app.post("/api/personenstandort/personen/{kennung}/pause")
async def person_pausieren(kennung: str, p: PersonPause, request: Request) -> dict:
    _gleiche_herkunft(request)
    try:
        await personendienst.pausieren(_person_kennung(kennung), p.pause)
    except personendienst.PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}
    return {"ok": True}


@app.delete("/api/personenstandort/personen/{kennung}")
async def person_entfernen(kennung: str, request: Request) -> dict:
    """Person UND ihr Standort weg — nur Eikes Tipp in der App (zweiter Tipp in der Oberfläche), kein Werkzeug für das Modell."""
    _gleiche_herkunft(request)
    try:
        await personendienst.entfernen(_person_kennung(kennung))
    except personendienst.PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}
    return {"ok": True}


def _anfrage_erledigt(a: dict) -> bool:
    """Angenommene Anfrage, deren Person schon als Kontakt steht (Friday legt sie nach der Annahme selbst an): nichts mehr zu tun."""
    return a.get("zustand") == "angenommen" and any(
        personenregister.nach_kanal("snapchat", n) for n in (a.get("snapchat"), a.get("titel")) if n)


@app.get("/api/kontakte")
def kontakte_liste() -> dict:
    """Personen, unbekannte Kontakte und die Werkzeuge, die einer fremden Person überhaupt wählbar sind.
    Je Person dazu Snapchat-Ampel, letzter Austausch und die Arbeitsweisen (Skills), die sie treffen."""
    jetzt = time.time()
    anfragen = snapchatdienst.anfragen()
    fehl = snapchatdienst.stand_fehlschlaege()
    letzte = speicher.standard().letzter_austausch(snapchatdienst.ART)
    an = konnektoren.benutzbar("snapchat")
    schreiben = an and konnektoren.schreiben_erlaubt("snapchat")
    personen = []
    for p in personenregister.liste():
        name = (p.get("kanaele") or {}).get("snapchat", "")
        k = personenregister.schluessel(name)
        anfrage = next((a for a in anfragen if personenregister.schluessel(a.get("snapchat", "")) == k), None) if k else None
        austausch = personenuebersicht.letzter_austausch(letzte.get(p["id"]), jetzt)
        personen.append({**p, "snapchat_stand": snapchatdienst.freundschaft_satz(name) if name else "",
                         "snapchat_status": personenuebersicht.snapchat_status(
                             p, stand=snapchatdienst.freundschaft(name) if name else None, fehlschlag=fehl.get(k),
                             anfrage=anfrage, anschluss_an=an, schreibrecht=schreiben, jetzt=jetzt, austausch=austausch),
                         "werkzeuge": personenmodule.werkzeuge_fuer(p), "module": personenmodule.von_person(p), "module_wirksam": sorted(personenmodule.wirksam(p)),
                         "garantie_profil": personenmodule.hat_garantie_profil(p),
                         "module_verfuegbar": sorted(personenmodule.VERFUEGBAR),
                         "flammen_modus": personenflammen.modus(),
                         "austausch": austausch,
                         "arbeitsweisen": personenuebersicht.arbeitsweisen(p)})
    return {"flammen_eike": personenflammen.eike_an(), "flammen_modus": personenflammen.modus(),
            "personen": personen, "unbekannt": personenunbekannt.liste(), "rueckfragen": personenrueckfragen.offene_mit_kollision(),
            "anfragen": [a for a in anfragen if not _anfrage_erledigt(a)], "quellen_vorschlaege": personenquellen.kandidaten(),
            "quellen": personenquellen.stand(), "snapchat": {"an": an, "schreiben": schreiben},
            # Plan-Werkzeuge (Stundenplan, Kalender) steuert der Hebel „Eikes Plan“, nicht ein Haken je Werkzeug
            "werkzeuge": [w for w in personenuebersicht.rechte_gruppiert() if w["name"] not in personenmodule.PLAN_WERKZEUGE["voll"]]}


class FlammeEike(BaseModel):
    an: bool


@app.post("/api/flamme-eike")
def flamme_eike_setzen(k: FlammeEike, request: Request) -> dict:
    """Hebel „Flamme mit mir“ — nur Eikes Tipp in der App, kein Werkzeug fürs Modell."""
    _gleiche_herkunft(request)
    personenflammen.eike_setzen(k.an)
    return {"ok": True, "an": personenflammen.eike_an()}


class FlammenModus(BaseModel):
    modus: str


@app.post("/api/flammen-modus")
def flammen_modus_setzen(k: FlammenModus, request: Request) -> dict:
    """Hauptschalter der Flammen (aus · trocken · an) — nur Eikes Tipp in der App, kein Werkzeug fürs Modell."""
    _gleiche_herkunft(request)
    try:
        personenflammen.modus_setzen(k.modus)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "modus": personenflammen.modus()}


@app.get("/api/kontakte/anfragen")
def kontakt_anfragen_liste() -> dict:
    """Offene und angenommene Kontakt-Anfragen — nur Eikes Blick in der App, kein Werkzeug fürs Modell."""
    return {"anfragen": snapchatdienst.anfragen()}


class AnfrageRueckzug(BaseModel):
    snapchat: str = Field(max_length=200)


@app.post("/api/kontakte/anfragen/zurueckziehen")
def kontakt_anfrage_zurueckziehen(a: AnfrageRueckzug, request: Request) -> dict:
    _gleiche_herkunft(request)
    text = snapchatdienst.anfrage_stornieren(a.snapchat)
    if text.startswith("Dazu liegt keine Anfrage"):
        return {"ok": False, "text": text}
    return {"ok": True, "text": text}


@app.get("/api/kontakte/bekannt")
async def kontakte_bekannt() -> dict:
    """Wen Friday schon kennt (Wissen, Familie, Garantie, WhatsApp, Bilder) — zur Auswahl beim Anlegen."""
    return await personenbekannt.liste()


@app.post("/api/kontakte")
def kontakt_anlegen(k: KontaktNeu, request: Request) -> dict:
    _gleiche_herkunft(request)
    # 04.10.2026, Eike: Personen immer mit Vor- und Nachnamen (keine Verwechslung).
    if len((k.name or "").split()) < 2:
        return {"ok": False, "text": "Nenn Vor- und Nachnamen, z.B. „Kontakt A“."}
    try:
        return {"ok": True, "person": personenregister.anlegen(k.name, k.snapchat, k.persona, k.werkzeuge, k.proaktiv, k.graph_name)}
    except PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}


@app.post("/api/kontakte/unbekannt/ignorieren")
def kontakt_unbekannt_ignorieren(k: KontaktUnbekannt, request: Request) -> dict:
    _gleiche_herkunft(request)
    personenunbekannt.ignorieren(k.kanal, k.anzeige)
    return {"ok": True}


@app.post("/api/kontakte/{pid}")
def kontakt_aendern(pid: str, k: KontaktAenderung, request: Request) -> dict:
    """Persona, Rechte, Pausieren — nur Eikes Tipp in der App, kein Werkzeug fürs Modell."""
    _gleiche_herkunft(request)
    if not re.fullmatch(r"p\d{1,6}", pid):
        return {"ok": False, "text": "Diese Person gibt es nicht."}
    if k.name is not None and len(k.name.split()) < 2:
        return {"ok": False, "text": "Nenn Vor- und Nachnamen, z.B. „Kontakt A“."}
    try:
        return {"ok": True, "person": personenregister.aendern(pid, von="app", **k.model_dump(exclude_none=True))}
    except PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}


@app.post("/api/kontakte/{pid}/freundschaft")
def kontakt_freundschaft_pruefen(pid: str, request: Request) -> dict:
    """Das Handy schaut in den Chat dieser Person und vermerkt den Freundschaftsstand — nur ansehen, nichts senden."""
    _gleiche_herkunft(request)
    profil = personenregister.holen(pid) if re.fullmatch(r"p\d{1,6}", pid) else None
    anzeige = ((profil or {}).get("kanaele") or {}).get("snapchat", "").strip()
    if not profil or not anzeige:
        return {"ok": False, "text": "Diese Person hat keinen Snapchat-Namen."}
    try:
        snapchatdienst.auftrag_legen("stand", snapchat=anzeige)
    except snapchatdienst.SnapchatFehler as exc:
        return {"ok": False, "text": str(exc)}
    return {"ok": True, "text": f"Das Handy schaut gleich in den Chat mit {profil['name']} — in etwa einer Minute hier neu laden."}


@app.post("/api/kontakte/{pid}/freigaben")
async def kontakt_freigeben(pid: str, f: FreigabeNeu, request: Request) -> dict:
    """Eike erlaubt Friday, dieser Person etwas zu sagen: Kopie in deren Graph-Gruppe. Kein Werkzeug fürs Modell."""
    _gleiche_herkunft(request)
    if not re.fullmatch(r"p\d{1,6}", pid):
        return {"ok": False, "text": "Diese Person gibt es nicht."}
    try:
        return {"ok": True, "freigabe": await personenfreigaben.freigeben(pid, f.text, f.anlass)}
    except PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}


@app.post("/api/kontakte/{pid}/freigaben/{fid}/widerruf")
async def kontakt_widerrufen(pid: str, fid: str, request: Request) -> dict:
    _gleiche_herkunft(request)
    if not re.fullmatch(r"p\d{1,6}", pid) or not re.fullmatch(r"f\d{1,6}", fid):
        return {"ok": False, "text": "Diese Freigabe gibt es nicht."}
    try:
        return {"ok": True, "freigabe": await personenfreigaben.widerrufen(pid, fid)}
    except PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}


@app.post("/api/kontakte/rueckfragen/{aid}/freigeben")
async def rueckfrage_freigeben(aid: str, a: RueckfrageAntwort, request: Request) -> dict:
    """Eike formuliert, was die Person erfahren darf — wird eine Freigabe. Kein Werkzeug fürs Modell."""
    _gleiche_herkunft(request)
    if not re.fullmatch(r"a\d{1,6}", aid):
        return {"ok": False, "text": "Diese Rückfrage gibt es nicht."}
    try:
        return {"ok": True, "rueckfrage": await personenrueckfragen.freigeben(aid, a.text)}
    except PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}


@app.post("/api/kontakte/rueckfragen/{aid}/ablehnen")
async def rueckfrage_ablehnen(aid: str, request: Request) -> dict:
    _gleiche_herkunft(request)
    if not re.fullmatch(r"a\d{1,6}", aid):
        return {"ok": False, "text": "Diese Rückfrage gibt es nicht."}
    try:
        return {"ok": True, "rueckfrage": personenrueckfragen.ablehnen(aid)}
    except PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}


@app.post("/api/kontakte/rueckfragen/{aid}/annehmen")
async def rueckfrage_annehmen(aid: str, request: Request) -> dict:
    """Eikes Ja zu einem Terminvorschlag — trägt sofort mit Vermerk ein. Kein Werkzeug fürs Modell."""
    _gleiche_herkunft(request)
    if not re.fullmatch(r"a\d{1,6}", aid):
        return {"ok": False, "text": "Diese Rückfrage gibt es nicht."}
    try:
        return {"ok": True, "rueckfrage": personenrueckfragen.annehmen(aid)}
    except PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}


class QuellenKnuepfen(BaseModel):
    quelle: str = Field(max_length=20)
    ref: Any = None


@app.post("/api/kontakte/{pid}/verknuepfen")
async def kontakt_verknuepfen(pid: str, k: QuellenKnuepfen, request: Request) -> dict:
    """Eikes Ja: Diese Quelle gehört zu dieser Person. Kein Werkzeug fürs Modell."""
    _gleiche_herkunft(request)
    if not re.fullmatch(r"p\d{1,6}", pid):
        return {"ok": False, "text": "Diese Person gibt es nicht."}
    try:
        person = personenquellen.verknuepfen(pid, k.quelle, k.ref)
        if k.quelle == "garantie":
            await personenquellen.profil_ins_gedaechtnis(pid)   # Adresse ins Wissen, nie IBAN
        return {"ok": True, "person": person}
    except PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}


class QuellenLoesen(BaseModel):
    quelle: str = Field(max_length=20)


@app.post("/api/kontakte/{pid}/entknuepfen")
async def kontakt_entknuepfen(pid: str, k: QuellenLoesen, request: Request) -> dict:
    """Verknüpfung lösen — Person und Quelle bleiben. Kein Werkzeug fürs Modell."""
    _gleiche_herkunft(request)
    if not re.fullmatch(r"p\d{1,6}", pid):
        return {"ok": False, "text": "Diese Person gibt es nicht."}
    try:
        person = personenquellen.entknuepfen(pid, k.quelle)
        if k.quelle == "garantie":
            await personenquellen.profil_aus_dem_gedaechtnis(pid)
        return {"ok": True, "person": person}
    except PersonenFehler as exc:
        return {"ok": False, "text": str(exc)}


@app.post("/api/dashboard/jellyfin/{befehl}")
async def dashboard_jellyfin(befehl: str, request: Request) -> dict:
    _gleiche_herkunft(request)
    try:
        await dashboardjellyfin.befehl(befehl)
    except ValueError:
        raise HTTPException(404, "Diesen Befehl gibt es nicht.") from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except jellyfindienst.JellyfinFehler as exc:
        raise HTTPException(502, str(exc)) from None
    return {"ok": True}


@app.post("/api/dashboard/pcmedien/{befehl}")
async def dashboard_pcmedien(befehl: str, request: Request) -> dict:
    _gleiche_herkunft(request)
    try:
        await dashboardpcmedien.befehl(befehl)
    except ValueError:
        raise HTTPException(404, "Diesen Befehl gibt es nicht.") from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except pcdienst.PcFehler as exc:
        raise HTTPException(502, str(exc)) from None
    return {"ok": True}


@app.get("/api/pcmedien/bild/{kennung}")
def pcmedien_bild(kennung: str) -> Response:
    """Vorschaubild der PC-Mediensitzung — vom Helfer geholt und hier gehalten (nur für Eikes App)."""
    b = re.fullmatch(r"[0-9a-f]{16}", kennung) and dashboardpcmedien.bild(kennung)
    if not b:
        raise HTTPException(404, "Kein Bild.")
    return Response(b[0], media_type=b[1])


@app.get("/api/jellyfin/bild/{item_id}")
async def jellyfin_bild(item_id: str) -> Response:
    """Vorschaubild für die Karte — Friday holt es selbst, der Schlüssel bleibt hier."""
    if not re.fullmatch(r"[0-9a-f]{32}", item_id):
        raise HTTPException(404, "Kein Bild.")
    if not konnektoren.benutzbar("jellyfin"):
        raise HTTPException(403, "Jellyfin ist auf der Freigaben-Seite nicht eingerichtet.")
    try:
        daten, typ = await jellyfindienst.bild(item_id)
    except jellyfindienst.JellyfinFehler as exc:
        raise HTTPException(502, str(exc)) from None
    return Response(daten, media_type=typ)      # Cache-Control setzt für alles `frisch_halten`


@app.delete("/api/wachen/{kennung}")
def wache_entfernen(kennung: str) -> dict:
    weg = wache.entfernen(kennung)
    if weg is None:
        raise HTTPException(404, "Diese Wache gibt es nicht.")
    return {"ok": True}


# ---------- Einstellungen: Morgenbriefing ----------
# Eine Einstellung, keine Freigabe: Das Briefing holt nur, was schon freigegeben
# ist. Friday kann es auch im Gespräch schalten (Werkzeug `morgenbriefing`).

class BriefingEinstellung(BaseModel):
    an: Optional[bool] = None
    uhrzeit: Optional[str] = Field(None, max_length=8)


def _briefing_stand() -> dict:
    return {"an": briefing.ist_an(), "uhrzeit": f"{briefing.uhrzeit():%H:%M}", "ort": wetter.heimatort(),
            # Was hineinkommt, hängt an den Freigaben — die Seite sagt, was fehlt.
            "teile": {"wetter": konnektoren.benutzbar("wetter"),
                      "schule": konnektoren.benutzbar("schulmanager"),
                      "termine": konnektoren.benutzbar("icloud_kalender")},
            "geraete": len(push.geraete())}


@app.get("/api/einstellungen/morgenbriefing")
def briefing_einstellung() -> dict:
    return _briefing_stand()


@app.post("/api/einstellungen/morgenbriefing")
def briefing_einstellen(e: BriefingEinstellung) -> dict:
    try:
        briefing.einstellen(an=e.an, uhrzeit_text=e.uhrzeit)
    except ValueError:
        raise HTTPException(400, "Die Uhrzeit bitte als HH:MM.") from None
    return _briefing_stand()


@app.get("/api/morgenbriefing/vorschau")
async def briefing_vorschau() -> dict:
    titel, text = await briefing.briefing(config.jetzt().date())
    return {"text": f"{titel}\n{text}"}


@app.get("/api/planer/abendblick/vorschau")
async def abendblick_vorschau() -> dict:
    morgen = config.jetzt().date() + timedelta(days=1)
    titel, text = await briefing.abendblick(morgen)
    return {"text": f"{titel}\n{text}"}


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if not live.origin_erlaubt(origin):
        log.warning("WebSocket abgelehnt — unerlaubter Origin: %s", origin)
        await websocket.close(code=1008)
        return
    await live.live_sitzung(websocket)


def _aufbau() -> tuple[str, str]:
    """(Stempel für die Adressen, lesbarer Stand) — der Zeitpunkt der neuesten Änderung an den Dateien der Seite."""
    neueste = max((p.stat().st_mtime for p in WEB.rglob("*") if p.is_file()), default=0)
    return str(int(neueste)), datetime.fromtimestamp(neueste, ZoneInfo(config.ZEITZONE)).strftime("%d.%m. %H:%M")


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def startseite() -> Response:
    """Die Startseite mit `?v=<Stempel>` an jedem Skript und Stylesheet: Ein geänderter Stand hat eine neue Adresse,
    also kann ihn kein Zwischenspeicher (auch nicht der einer installierten App auf dem iPhone) verdecken.
    Dazu der Stand der Dateien als <meta name="build"> — die Kopfzeile zeigt ihn an."""
    stempel, stand = _aufbau()
    seite = (WEB / "index.html").read_text(encoding="utf-8")
    seite = re.sub(r'((?:src|href)=")(/[\w.\-/]+\.(?:js|css))(")', rf"\1\2?v={stempel}\3", seite)
    seite = seite.replace("<head>", f'<head>\n  <meta name="build" content="{stand}">', 1)
    return Response(seite, media_type="text/html; charset=utf-8")


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
