"""WhatsApp — Friday liest die Chats, die Eike freigegeben hat.

Der Weg dorthin ist WAHA (`devlikeapro/waha`), ein eigener Container im internen
Netz, der WhatsApp als REST-Schnittstelle anbietet. Friday spricht nur mit ihm,
nie direkt mit WhatsApp. Gewählt am 20.09.2026, weil es als Container in diesen
Stack passt (wie `studio`), keine Node- oder Go-Abhängigkeiten ins Friday-Abbild
bringt und seit Version 2026.6.1 vollständig im freien Abbild steckt.

Maschine: **GOWS** (whatsmeow, Go, WebSocket). Kein Chromium, und Chatliste sowie
Nachrichten kann sie von Haus aus — anders als NOWEB, die dafür erst einen Store
braucht. WEBJS wäre ein vollständiger Browser für einen Heimserver zu viel.

Was hier gilt (und warum):

- **Eike gibt jeden Chat einzeln frei.** Die Liste steht in
  `data/whatsapp_chats.json` (600). Gesperrt wird HIER im Dienst, nicht erst im
  Werkzeug: Auch ein fehlerhaftes Werkzeug kommt so an keinen fremden Chat.
  Dasselbe Muster wie bei den Spotify-Geräten (14.09.2026).
- **Senden ist ein ZWEITER Riegel.** Lesen und Antworten sind getrennt: Ein Chat
  wird eigens fürs Senden angekreuzt (`senden` in derselben Datei), UND der
  Konnektor braucht das Schreibrecht, UND Eike muss jedem einzelnen Text
  ausdrücklich zustimmen (`kern/werkzeuge/whatsapp.py`). Fällt eines davon weg,
  geht nichts hinaus. Dazu eine Obergrenze je Stunde — ein Modell im Kreis darf
  nicht Eikes Kontakte zuschütten.
  Gemessen am 20.09.2026: `POST /api/sendText` nimmt `{session, chatId, text}`.
  GOWS prüft den Text NICHT — ein leeres Feld wird trotzdem verschickt. Deshalb
  prüft Friday ihn selbst, bevor sie ruft.
- **Chatinhalte sind fremder Text.** Was hier herauskommt, sind Worte anderer
  Menschen. Die Werkzeuge stehen deshalb in `FREMDE_WERKZEUGE`.
- **Nichts wird gespeichert.** Gelesen wird live bei WAHA; in Fridays Daten
  landet nur, welche Chats freigegeben sind. Die ausgewerteten VERLÄUFE, die
  Eike selbst hochlädt, sind etwas anderes (`kern/chats/`).

Beim Koppeln zu beachten: WAHA dreht den QR-Code alle **20 Sekunden** weiter, und
nach wenigen Runden läuft die ganze Reihe ab (`Failed QR item event: timeout`,
Sitzung dann FAILED). Ein Code, den die Oberfläche einmal lädt und stehen lässt,
ist beim Scannen längst tot — WhatsApp meldet dann „Verbindung abgebrochen".
Deshalb holt die Freigaben-Seite den Code laufend neu und startet die Sitzung von
selbst neu, wenn sie abgelaufen ist.

Offen und bewusst nicht gebaut: Medien herunterladen, Webhooks für eintreffende
Nachrichten, und `sendSeen` — Friday setzt keine blauen Haken.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import threading
import time
from datetime import datetime, time as tageszeit, timezone

import httpx

log = logging.getLogger("friday.whatsapp")

KONNEKTOR = "whatsapp"
SITZUNG = "default"           # WAHA Core kennt genau diese eine
ZEITLIMIT = 20
MAX_CHATS = 100
MAX_NACHRICHTEN = 50

# Senden: eng gefasst. Länger als das ist keine Antwort mehr, sondern ein Aufsatz;
# mehr als ein paar je Stunde ist kein Gespräch mehr, sondern ein Skript.
MAX_TEXT = 1000
SENDE_GRENZE = 8
SENDE_FENSTER_S = 3600

# Gelesene Nachrichten gelten eine Stunde als frisch (Eikes Vorgabe, 20.09.2026).
# Innerhalb davon antwortet Friday aus dem Zwischenspeicher, statt WhatsApp erneut
# zu fragen. Das hat drei Gründe: Ein Gespräch fragt oft mehrmals nach demselben
# Chat; jede Abfrage ist eine Bewegung, die WhatsApp sieht (und automatisches
# Verhalten ist genau das, was Sperren auslöst); und die Antwort kommt sofort.
MERKEN_S = 3600

# ---------- Der Hintergrundlauf ----------
# Eike wollte, dass Friday die freigegebenen Chats von selbst im Blick hat
# (20.09.2026). Das ist mehr Bewegung Richtung WhatsApp als ein Abruf auf Frage,
# also ist er darauf ausgelegt, wie ein Mensch auszusehen und nicht wie ein Skript.
# Was Sperren auslöst, ist laut den Auswertungen von 2025/26 vor allem der TAKT:
# immer zur selben Sekunde, rund um die Uhr, alles auf einmal.
TAKT_S = 3600                 # einmal je Stunde — nicht öfter, der Speicher gilt ohnehin so lange
STREUUNG_S = 600              # ±10 Minuten Zufall: nie zur selben Minute
PAUSE_CHAT_S = (4.0, 12.0)    # zwischen zwei Chats, zufällig — kein Stakkato
WACH_AB = tageszeit(7, 0)     # nachts wird nicht abgerufen: niemand liest um 4 Uhr Chats
WACH_BIS = tageszeit(23, 0)
MAX_CHATS_JE_LAUF = 12        # mehr als das wäre auffällig
FEHLER_PAUSE_S = 900

# Woran man eine Gruppe erkennt: WhatsApp hängt es an die Kennung.
GRUPPE = "@g.us"
PERSON = "@c.us"

_sperre = threading.Lock()
# Nur im Arbeitsspeicher — auf der Platte steht weiterhin NUR, welche Chats frei
# sind. Nach einem Neustart wird wieder frisch gelesen.
_zwischen: dict[str, dict] = {}


class WhatsAppFehler(RuntimeError):
    """Vorlesbar: sagt, was Eike tun muss."""


def basis() -> str:
    return os.getenv("WAHA_URL", "http://waha:3000").rstrip("/")


def _kopf() -> dict:
    schluessel = os.getenv("WAHA_SCHLUESSEL", "").strip()
    return {"X-Api-Key": schluessel} if schluessel else {}


async def _ruf(methode: str, pfad: str, params: dict | None = None,
               rumpf: dict | None = None) -> dict | list:
    try:
        async with httpx.AsyncClient(timeout=ZEITLIMIT) as client:
            antwort = await client.request(methode, basis() + pfad, headers=_kopf(),
                                           params=params or {}, json=rumpf)
    except httpx.HTTPError as exc:
        raise WhatsAppFehler("Der WhatsApp-Dienst antwortet gerade nicht. Läuft der Container?") from exc
    if antwort.status_code == 401:
        raise WhatsAppFehler("Der WhatsApp-Dienst weist den Zugang ab (falscher WAHA_SCHLUESSEL).")
    if antwort.status_code == 404:
        raise WhatsAppFehler("Das kennt der WhatsApp-Dienst nicht. Ist die Sitzung gestartet?")
    if antwort.status_code >= 400:
        raise WhatsAppFehler(f"Der WhatsApp-Dienst antwortet mit {antwort.status_code}.")
    if not antwort.content:
        return {}
    try:
        return antwort.json()
    except ValueError:
        return {}


# ---------- Sitzung ----------
# STARTING → SCAN_QR_CODE → WORKING. Dazwischen hängt Eikes Handy: Er koppelt
# Friday einmal als Gerät, danach läuft es weiter.

ZUSTAende = {
    "STOPPED": "nicht gestartet",
    "STARTING": "startet gerade",
    "SCAN_QR_CODE": "wartet auf den QR-Code",
    "WORKING": "verbunden",
    "FAILED": "gescheitert",
}


async def status() -> dict:
    """Wie steht es um die Verbindung? Ohne Sitzung: „nicht gestartet“."""
    try:
        daten = await _ruf("GET", f"/api/sessions/{SITZUNG}")
    except WhatsAppFehler as exc:
        if "Ist die Sitzung gestartet" in str(exc):
            return {"zustand": "STOPPED", "text": ZUSTAende["STOPPED"], "nummer": ""}
        raise
    zustand = str((daten or {}).get("status") or "STOPPED")
    ich = (daten or {}).get("me") or {}
    return {"zustand": zustand, "text": ZUSTAende.get(zustand, zustand),
            "nummer": str(ich.get("pushName") or ich.get("id") or "")}


async def starten() -> dict:
    """Sitzung anlegen bzw. starten. Danach zeigt `qr_bild()` den Code zum Koppeln.

    Eine GESCHEITERTE Sitzung muss erst gestoppt werden: `POST /start` allein
    antwortet zwar mit 201, lässt sie aber auf FAILED stehen (gemessen 20.09.2026).
    Stoppen und neu starten bringt sie nach ~2 s zurück auf SCAN_QR_CODE.
    """
    stand = await status()
    if stand["zustand"] in ("FAILED", "STOPPED"):
        try:
            await _ruf("POST", f"/api/sessions/{SITZUNG}/stop")
        except WhatsAppFehler:
            pass                      # gab es noch nicht — dann ist nichts zu stoppen
    try:
        await _ruf("POST", "/api/sessions", rumpf={"name": SITZUNG, "start": True})
    except WhatsAppFehler as exc:
        # Gibt es sie schon, ist das kein Fehler — dann nur starten.
        if "422" not in str(exc) and "409" not in str(exc):
            raise
        await _ruf("POST", f"/api/sessions/{SITZUNG}/start")
    return await status()


async def trennen() -> None:
    """Abmelden und Sitzung stoppen. Danach ist Friday als Gerät wieder weg."""
    try:
        await _ruf("POST", f"/api/sessions/{SITZUNG}/logout")
    except WhatsAppFehler:
        pass
    await _ruf("POST", f"/api/sessions/{SITZUNG}/stop")


async def qr_bild() -> bytes:
    """Der QR-Code als PNG — den scannt Eike in WhatsApp unter „Gerät koppeln“."""
    try:
        async with httpx.AsyncClient(timeout=ZEITLIMIT) as client:
            antwort = await client.get(f"{basis()}/api/{SITZUNG}/auth/qr",
                                       headers={**_kopf(), "Accept": "image/png"},
                                       params={"format": "image"})
    except httpx.HTTPError as exc:
        raise WhatsAppFehler("Der WhatsApp-Dienst antwortet gerade nicht.") from exc
    if antwort.status_code != 200 or not antwort.content:
        # 422 heißt: die Sitzung wartet gerade nicht auf einen Code (abgelaufen oder
        # schon verbunden). Die Oberfläche startet dann neu, statt ein totes Bild zu zeigen.
        raise WhatsAppFehler("Gerade gibt es keinen QR-Code. Die Sitzung muss neu gestartet werden.")
    return antwort.content


# ---------- Welche Chats darf Friday lesen? ----------
# Eike kreuzt sie auf der Freigaben-Seite an. Ohne Häkchen kommt Friday an
# keinen einzigen — auch nicht an einen, dessen Kennung sie zufällig kennt.

def _datei():
    from kern import config
    return config.daten_ordner() / "whatsapp_chats.json"


def frei() -> list[dict]:
    """Die freigegebenen Chats: [{id, name}]."""
    try:
        d = json.loads(_datei().read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def frei_setzen(liste: list[dict]) -> None:
    sauber, gesehen = [], set()
    for c in liste or []:
        kennung = str(c.get("id", ""))[:120].strip()
        if not kennung or kennung in gesehen:
            continue
        gesehen.add(kennung)
        # `senden` ist die ZWEITE Freigabe und muss ausdrücklich dastehen. Ein Chat,
        # der nur zum Lesen angekreuzt wird, bekommt sie nicht nebenbei.
        sauber.append({"id": kennung, "name": str(c.get("name", ""))[:120],
                       "senden": c.get("senden") is True})
    ziel = _datei()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    with _sperre:
        fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(sauber, f, ensure_ascii=False)
        os.chmod(neu, 0o600)
        os.replace(neu, ziel)
    # Ein zurückgenommenes Häkchen muss SOFORT wirken — auch für das, was schon
    # gelesen wurde. Deshalb hier alles verwerfen, nicht nur die entfernten Chats.
    vergessen()


def vergessen() -> None:
    """Zwischenspeicher leeren. Beim Ändern der Freigaben und für Tests."""
    with _sperre:
        _zwischen.clear()


def ist_frei(chat_id: str) -> bool:
    return bool(chat_id) and any(c["id"] == chat_id for c in frei())


def _pruefen(chat_id: str) -> dict:
    """Der Riegel. Hier, nicht im Werkzeug — damit ihn niemand umgeht."""
    for c in frei():
        if c["id"] == chat_id:
            return c
    raise WhatsAppFehler("Diesen Chat hat Eike nicht freigegeben. Auf der Freigaben-Seite "
                         "unter WhatsApp ankreuzen, dann lese ich ihn.")


def darf_senden(chat_id: str) -> bool:
    """Lesen heißt nicht antworten: dafür gibt es ein zweites Häkchen."""
    return bool(chat_id) and any(c["id"] == chat_id and c.get("senden") is True for c in frei())


def _pruefen_senden(chat_id: str) -> dict:
    """Der zweite Riegel — auch er hier im Dienst, nicht erst im Werkzeug."""
    chat = _pruefen(chat_id)                      # erst überhaupt freigegeben?
    if chat.get("senden") is not True:
        raise WhatsAppFehler(f"In „{chat.get('name') or chat_id}“ darf ich nicht schreiben. "
                             "Auf der Freigaben-Seite steht neben dem Chat ein zweites Häkchen "
                             "fürs Antworten — ohne das lese ich nur.")
    from kern import konnektoren
    if not konnektoren.schreiben_erlaubt(KONNEKTOR):
        raise WhatsAppFehler("Das Schreibrecht für WhatsApp ist aus. Auf der Freigaben-Seite "
                             "unter WhatsApp einschalten.")
    return chat


def finden(wunsch: str) -> list[dict]:
    """Freigegebene Chats, die zum Namen passen — genaue Treffer zuerst.

    Nur unter den FREIGEGEBENEN: Ein Name, den Eike nicht freigegeben hat, findet
    hier nichts, statt eine Fehlermeldung mit fremden Namen zu erzeugen.
    """
    gesucht = (wunsch or "").casefold().strip()
    if not gesucht:
        return []
    treffer = []
    for c in frei():
        name = (c.get("name") or "").casefold()
        if name == gesucht:
            treffer.append((0, c))
        elif len(gesucht) >= 3 and gesucht in name:
            treffer.append((1, c))
    treffer.sort(key=lambda x: x[0])
    return [c for _, c in treffer]


# ---------- Lesen ----------

def _zeit(wert) -> str:
    try:
        return datetime.fromtimestamp(int(wert), timezone.utc).astimezone().strftime("%d.%m. %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _stempel(wert) -> int:
    try:
        return int(wert or 0)
    except (TypeError, ValueError):
        return 0


def _absender(m: dict, partner: str = "") -> str:
    """Wer hat geschrieben? Gemessen 21.09.2026 (GOWS-Engine): Der Name steht in
    `_data.Info.PushName`, NICHT in `_data.notifyName` — ohne das stand vor jeder
    fremden Nachricht „?“, und Friday verwechselte Kontakt Fs Worte mit Eikes. Im
    Einzelchat ist der Absender ohnehin der Chatpartner."""
    if m.get("fromMe"):
        return "Eike"
    daten = m.get("_data") or {}
    info = daten.get("Info") or {} if isinstance(daten, dict) else {}
    name = (daten.get("notifyName") if isinstance(daten, dict) else "") or info.get("PushName") or ""
    return str(name).strip() or partner


def _nachricht(m: dict, partner: str = "") -> dict:
    """`partner`: Name des Einzelchats, als Absender, wenn die Nachricht keinen trägt."""
    m = m or {}
    von = _absender(m, partner)
    return {"id": str(m.get("id", "")), "zeit": _zeit(m.get("timestamp")),
            "stempel": _stempel(m.get("timestamp")),
            "von": von,
            "von_mir": bool(m.get("fromMe")),
            "text": str(m.get("body") or "")[:2000],
            "medien": bool(m.get("hasMedia"))}


async def alle_chats(anzahl: int = MAX_CHATS) -> list[dict]:
    """ALLE Chats — nur für die Freigaben-Seite, damit Eike auswählen kann.

    Kein Werkzeug ruft das auf: Das Modell bekommt nie die ganze Liste zu sehen,
    sondern nur, was freigegeben ist.
    """
    daten = await _ruf("GET", f"/api/{SITZUNG}/chats/overview",
                       params={"limit": max(1, min(int(anzahl), MAX_CHATS)), "offset": 0})
    liste = daten if isinstance(daten, list) else (daten or {}).get("chats") or []
    erlaubt = {c["id"] for c in frei()}
    darf_schreiben = {c["id"] for c in frei() if c.get("senden") is True}
    raus = []
    for c in liste:
        kennung = str((c or {}).get("id") or "")
        if not kennung:
            continue
        raus.append({"id": kennung, "name": str(c.get("name") or kennung.split("@")[0]),
                     "gruppe": kennung.endswith(GRUPPE), "frei": kennung in erlaubt,
                     "senden": kennung in darf_schreiben})
    return raus


async def nachrichten(chat_id: str, anzahl: int = 20, frisch: bool = False) -> dict:
    """Die letzten Nachrichten eines FREIGEGEBENEN Chats, älteste zuerst.

    Aus dem Zwischenspeicher, solange er jünger als `MERKEN_S` ist und genug
    Nachrichten enthält. `alter_s` sagt, wie alt der Stand ist — Friday sagt das
    weiter, statt einen Stand von vor einer Stunde als „jetzt" auszugeben.
    """
    chat = _pruefen(chat_id)
    anzahl = max(1, min(int(anzahl), MAX_NACHRICHTEN))
    with _sperre:
        gemerkt = _zwischen.get(chat_id)
    if (not frisch and gemerkt and time.monotonic() - gemerkt["zeitpunkt"] < MERKEN_S
            and gemerkt["anzahl"] >= anzahl):
        return {"chat": chat.get("name") or chat_id, "id": chat_id,
                "nachrichten": gemerkt["nachrichten"][-anzahl:],
                "alter_s": int(time.monotonic() - gemerkt["zeitpunkt"])}

    daten = await _ruf("GET", f"/api/{SITZUNG}/chats/{chat_id}/messages",
                       params={"limit": anzahl, "downloadMedia": "false"})
    liste = daten if isinstance(daten, list) else (daten or {}).get("messages") or []
    partner = "" if chat_id.endswith(GRUPPE) else (chat.get("name") or "")
    nachr = [_nachricht(m, partner) for m in liste if m]
    nachr = [n for n in nachr if n["text"] or n["medien"]]
    # WAHA liefert neueste zuerst. Nach dem Stempel sortieren, nicht nach „TT.MM.“:
    # als Text stand der 30.08. hinter dem 01.09.
    nachr.sort(key=lambda n: n["stempel"])
    with _sperre:
        # Nur behalten, was noch freigegeben ist: Zwischen Abruf und Ablage kann
        # das Häkchen gefallen sein.
        if any(c["id"] == chat_id for c in frei()):
            _zwischen[chat_id] = {"zeitpunkt": time.monotonic(), "anzahl": anzahl,
                                  "nachrichten": nachr}
    return {"chat": chat.get("name") or chat_id, "id": chat_id, "nachrichten": nachr, "alter_s": 0}


# ---------- Senden ----------
# Drei Riegel, alle im Server: der Chat muss fürs Senden angekreuzt sein
# (`_pruefen_senden`), der Konnektor braucht das Schreibrecht, und das Werkzeug
# verlangt Eikes ausdrückliches Ja. Dazu die Stundengrenze hier — sie fängt den
# Fall ab, dass ein Modell in eine Schleife läuft und niemand zusieht.

_gesendet: list[float] = []


def sende_zaehler() -> int:
    """Wie viele Nachrichten in der laufenden Stunde hinausgegangen sind."""
    jetzt = time.monotonic()
    with _sperre:
        _gesendet[:] = [t for t in _gesendet if jetzt - t < SENDE_FENSTER_S]
        return len(_gesendet)


def sende_zaehler_leeren() -> None:
    with _sperre:
        _gesendet.clear()


def text_pruefen(text: str) -> str:
    """Den Text selbst prüfen — WAHA tut es nicht.

    Gemessen 20.09.2026: `POST /api/sendText` ohne `text` läuft bis zur
    Nummernsuche durch, statt 400 zu antworten. Eine leere Nachricht würde also
    wirklich hinausgehen."""
    sauber = (text or "").strip()
    if not sauber:
        raise WhatsAppFehler("Ohne Text schicke ich nichts.")
    if len(sauber) > MAX_TEXT:
        raise WhatsAppFehler(f"Der Text ist zu lang ({len(sauber)} Zeichen, erlaubt sind {MAX_TEXT}).")
    return sauber


FRIDAY_ZEICHEN = "🤖"        # Kennzeichen am Ende jeder Nachricht von Friday (15.10.2026)


def mit_zeichen(text: str) -> str:
    """Fridays Kennzeichen ans Ende — einmal, nicht doppelt."""
    t = (text or "").strip()
    return t if t.endswith(FRIDAY_ZEICHEN) else f"{t} {FRIDAY_ZEICHEN}"


def von_friday(nachricht: dict) -> bool:
    """Trägt die Nachricht Fridays Kennzeichen am Ende?"""
    return str((nachricht or {}).get("text") or "").rstrip().endswith(FRIDAY_ZEICHEN)


async def senden(chat_id: str, text: str) -> dict:
    """Eine Nachricht in einen freigegebenen Chat. Nur über das Werkzeug mit Eikes Ja.

    Nach dem Senden wird der Zwischenspeicher dieses Chats verworfen: Sonst läse
    Friday bis zu eine Stunde lang einen Stand, in dem ihre eigene Nachricht fehlt.
    """
    chat = _pruefen_senden(chat_id)
    sauber = mit_zeichen(text_pruefen(text))
    if len(sauber) > MAX_TEXT:
        raise WhatsAppFehler(f"Der Text ist zu lang ({len(sauber)} Zeichen mit meinem Zeichen, erlaubt sind {MAX_TEXT}).")
    if sende_zaehler() >= SENDE_GRENZE:
        raise WhatsAppFehler(f"Ich habe in dieser Stunde schon {SENDE_GRENZE} Nachrichten "
                             "verschickt. Das reicht — schreib den Rest bitte selbst.")
    await _ruf("POST", "/api/sendText",
               rumpf={"session": SITZUNG, "chatId": chat_id, "text": sauber})
    with _sperre:
        _gesendet.append(time.monotonic())
        _zwischen.pop(chat_id, None)
    log.info("WhatsApp-Nachricht gesendet (%d Zeichen)", len(sauber))
    return {"chat": chat.get("name") or chat_id, "id": chat_id, "text": sauber}


# ---------- Von selbst nachsehen ----------

_gesehen: dict[str, int] = {}          # je Chat der neueste Zeitstempel beim letzten Lauf
_letzter_lauf: dict = {}


def wach(jetzt: datetime | None = None) -> bool:
    """Ist gerade eine Zeit, zu der ein Mensch auf sein Handy schaut?"""
    h = (jetzt or datetime.now()).astimezone().time()
    return WACH_AB <= h < WACH_BIS


def stand() -> dict:
    """Was der letzte Lauf getan hat — für die Freigaben-Seite und Tests."""
    return dict(_letzter_lauf)


def neues() -> list[dict]:
    """Je freigegebenem Chat: wie viel seit dem vorletzten Lauf dazukam.

    Nur ZAHLEN und Namen, kein Inhalt — das ist der Teil, der ins Morgenbriefing
    darf. Eine Push-Nachricht mit fremden Chatinhalten auf dem Sperrbildschirm
    will niemand.
    """
    raus = []
    with _sperre:
        for c in frei():
            gemerkt = _zwischen.get(c["id"])
            if not gemerkt:
                continue
            grenze = _gesehen.get(c["id"])
            if grenze is None:
                continue                       # erster Lauf: es gibt kein „davor"
            anzahl = sum(1 for n in gemerkt["nachrichten"]
                         if n["stempel"] > grenze and not n["von_mir"])
            if anzahl:
                raus.append({"name": c.get("name") or c["id"].split("@")[0], "anzahl": anzahl})
    return raus


async def durchgang(pause: tuple[float, float] | None = None) -> dict:
    """Einmal alle freigegebenen Chats auffrischen — nacheinander, mit Pausen.

    Holt NUR freigegebene Chats; die vollständige Chatliste wird im Hintergrund
    nie abgefragt. Markiert nichts als gelesen: Es geht ausschließlich ein
    lesendes `GET …/messages` hinaus, nie `sendSeen` — Eikes Kontakte sehen
    also keine blauen Haken, weil Friday nachgesehen hat.
    """
    global _letzter_lauf
    unten, oben = pause or PAUSE_CHAT_S
    offen = frei()[:MAX_CHATS_JE_LAUF]
    geholt, fehler = 0, ""
    for i, c in enumerate(offen):
        try:
            stand_chat = await nachrichten(c["id"], 20, frisch=True)
        except WhatsAppFehler as exc:
            fehler = str(exc)
            break                              # antwortet der Dienst nicht, hört es auf
        except Exception as exc:
            fehler = f"{type(exc).__name__}"
            break
        neuester = max((n["stempel"] for n in stand_chat["nachrichten"]), default=0)
        with _sperre:
            _gesehen.setdefault(c["id"], neuester)
        geholt += 1
        if i + 1 < len(offen):
            await asyncio.sleep(random.uniform(unten, oben))
    _letzter_lauf = {"zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "chats": geholt, "fehler": fehler}
    return _letzter_lauf


def _gesehen_nachziehen() -> None:
    """Nach dem Lauf gilt der jetzige Stand als gesehen."""
    with _sperre:
        for c in frei():
            gemerkt = _zwischen.get(c["id"])
            if gemerkt:
                _gesehen[c["id"]] = max((n["stempel"] for n in gemerkt["nachrichten"]), default=0)


async def dauerlauf() -> None:
    """Stündlich nachsehen — mit Streuung, nur tagsüber, nur wenn freigegeben.

    Abschaltbar über `WHATSAPP_HINTERGRUND=aus` in der .env, unabhängig vom
    Konnektor: lesen auf Frage kann man wollen, ohne dass Friday von selbst nachsieht.
    """
    from kern import konnektoren
    await asyncio.sleep(240)                   # erst den Start in Ruhe lassen
    while True:
        an = os.getenv("WHATSAPP_HINTERGRUND", "an").strip().lower() != "aus"
        try:
            if an and konnektoren.benutzbar(KONNEKTOR) and frei() and wach():
                if (await status())["zustand"] == "WORKING":
                    ergebnis = await durchgang()
                    _gesehen_nachziehen()
                    if ergebnis["fehler"]:
                        log.warning("WhatsApp-Lauf abgebrochen: %s", ergebnis["fehler"])
                        await asyncio.sleep(FEHLER_PAUSE_S)
        except Exception as exc:               # ein Fehler darf den Lauf nie töten
            log.warning("WhatsApp-Lauf gescheitert (%s)", type(exc).__name__)
            await asyncio.sleep(FEHLER_PAUSE_S)
        # Nie zur selben Minute: der Takt ist das, was ein Skript verrät.
        await asyncio.sleep(TAKT_S + random.uniform(-STREUUNG_S, STREUUNG_S))


# ---------- Regeln: eigener, engerer Takt nur für Chats mit aktiver Regel (15.10.2026) ----------
# Eike wollte, dass „Kontakt F antwortet“ nicht erst nach einer Stunde bemerkt wird. Nur Chats, die eine aktive Regel beobachtet, werden
# öfter nachgesehen (alle ~10 Minuten, mit Streuung, nur in der Wach-Zeit, nur lesend, nie `sendSeen`, Pause zwischen den Chats).
REGEL_TAKT_S = 600
REGEL_STREUUNG_S = 90
REGEL_MAX_ALTER_S = 3600          # ältere Nachrichten lösen nie etwas aus (Neustart, lange nicht beobachteter Chat)

_regel_gesehen: dict[str, int] = {}      # je Chat der neueste gesehene Zeitstempel; Platte: `regeln_whatsapp.json`


def _regel_merker_pfad():
    from kern import config
    return config.daten_ordner() / "regeln_whatsapp.json"


def _regel_merker_laden() -> None:
    try:
        d = json.loads(_regel_merker_pfad().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(k, str) and isinstance(v, int) and k not in _regel_gesehen:
                _regel_gesehen[k] = v


def _regel_merker_speichern() -> None:
    pfad = _regel_merker_pfad()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    neu = pfad.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(_regel_gesehen, f)
    os.replace(neu, pfad)


async def regel_durchgang(pause: tuple[float, float] | None = None) -> dict:
    """Einmal die Chats mit aktiver WhatsApp-Regel auffrischen und neue Nachrichten von anderen an die Regeln melden.
    Eikes und Fridays eigene Nachrichten (`von_mir`, 🤖 am Ende) und alte lösen nichts aus; der erste Lauf je Chat setzt nur den Merker."""
    from kern.regeln import lauf, speicher
    unten, oben = pause or PAUSE_CHAT_S
    aktive = speicher.aktive("whatsapp")
    chats = list(dict.fromkeys((r["ausloeser"] or {}).get("chat_id") for r in aktive))
    chats = [c for c in chats if c and any(f["id"] == c for f in frei())][:MAX_CHATS_JE_LAUF]
    seit_aktivierung = {}            # je Chat der früheste Bestätigungszeitpunkt: davor Geschriebenes löst nie etwas aus
    for r in aktive:
        try:
            t = datetime.fromisoformat(r.get("bestaetigt") or "").timestamp()
        except (TypeError, ValueError):
            t = 0.0
        c = (r["ausloeser"] or {}).get("chat_id")
        seit_aktivierung[c] = min(t, seit_aktivierung.get(c, t))
    _regel_merker_laden()
    gemeldet, fehler = 0, ""
    for i, cid in enumerate(chats):
        try:
            stand_chat = await nachrichten(cid, 20, frisch=True)
        except Exception as exc:  # noqa: BLE001 — antwortet der Dienst nicht, hört der Durchgang auf
            fehler = f"{type(exc).__name__}"
            break
        nachr = sorted(stand_chat["nachrichten"], key=lambda m: m["stempel"])
        neuester = max((m["stempel"] for m in nachr), default=0)
        grenze = max(_regel_gesehen.get(cid) or 0, seit_aktivierung.get(cid, 0.0))
        _regel_gesehen[cid] = max(_regel_gesehen.get(cid) or 0, neuester)
        _regel_merker_speichern()
        jetzt = time.time()
        for m in nachr:
            if (m["stempel"] <= grenze or m["von_mir"] or von_friday(m) or not m["text"]
                    or jetzt - m["stempel"] > REGEL_MAX_ALTER_S):
                continue
            try:
                await lauf.ereignis("whatsapp", cid, m["von"], m["text"], m["id"], zeit=m["stempel"])
                gemeldet += 1
            except Exception:  # noqa: BLE001 — ein Regel-Fehler darf den Durchgang nie kippen
                log.exception("Regel-Ereignis für %s gescheitert", cid)
        if i + 1 < len(chats):
            await asyncio.sleep(random.uniform(unten, oben))
    return {"chats": len(chats), "gemeldet": gemeldet, "fehler": fehler}


async def regel_schritt() -> str:
    """Ein Takt des Regel-Laufs: „ruhe“ (nicht wach/Konnektor aus), „keine“ (keine aktive Regel), „getrennt“ (WhatsApp nicht verbunden) oder „ok“."""
    from kern import konnektoren
    from kern.regeln import speicher
    if not konnektoren.benutzbar(KONNEKTOR) or not wach():
        return "ruhe"
    if not speicher.aktive("whatsapp"):
        return "keine"
    if (await status())["zustand"] != "WORKING":
        return "getrennt"
    await regel_durchgang()
    return "ok"


async def regel_dauerlauf() -> None:
    await asyncio.sleep(300)
    while True:
        try:
            if os.getenv("WHATSAPP_HINTERGRUND", "an").strip().lower() != "aus":
                await regel_schritt()
        except Exception as exc:  # noqa: BLE001 — ein Fehler darf den Lauf nie töten
            log.warning("WhatsApp-Regel-Lauf gescheitert (%s)", type(exc).__name__)
            await asyncio.sleep(FEHLER_PAUSE_S)
        await asyncio.sleep(REGEL_TAKT_S + random.uniform(-REGEL_STREUUNG_S, REGEL_STREUUNG_S))
