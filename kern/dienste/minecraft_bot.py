"""Minecraft-Bot (Mindcraft) auf hphome: Zustand, Protokoll und Steuerung (29.10.2026).

Der Bot läuft als eigener Container neben Friday (`friday-mc-mindcraft-1`, Compose-Projekt `friday-mc`).
Friday selbst hat keinen Docker-Zugriff. Darum geht alles über den bestehenden Helfer auf hphome
(`kern/dienste/hphome.py`): Containerzustand lesen, Logs holen, starten/stoppen/neu starten. Eine neue
Tür zum Docker-Socket gibt es dafür bewusst nicht.

Was der Bot IM SPIEL tut, verrät nur sein MindServer — und der spricht **socket.io, nicht HTTP**
(gemessen am 29.10.2026: `/api/agents` antwortet 404). Den Handshake und die Ereignisse macht
`kern/dienste/mindserver.py`; hier werden sie nur ausgewertet:

* `agents-status` kommt sofort nach dem Verbinden: welche Bots es gibt und ob sie im Spiel sind.
* `listen-to-agents` schaltet den Sekundentakt ein, der `state-update` mit dem vollen Spielzustand
  schickt (Ort, Dimension, Leben, Hunger, Inventar, laufende Aktion).
* `send-message` gibt einem Bot eine Aufgabe; der MindServer nimmt `(name, {"from": ..., "message": ...})`.

Die Adresse des MindServers ist nicht fest (Port und Veröffentlichung gehören zum echten Setup auf
hphome). Steht sie in `MINECRAFT_BOT_URL`, gilt die ausdrücklich. Sonst liest Friday sie aus der
Konfiguration des Bot-Containers, die der hphome-Helfer ohnehin mitliefert: Der Host-Port, der auf den
MindServer-Port im Container zeigt, ergibt `http://host.docker.internal:<Port>`. Passt keine Zuordnung,
sagt der Status das ehrlich, statt einen Port zu raten. Der Containername lässt sich mit
`MINECRAFT_BOT_CONTAINER` überschreiben.

Wichtig für die Adresse: Der Port muss von Fridays Netz aus erreichbar sein (veröffentlicht auf der
Docker-Brücke oder gemeinsames Netz mit dem Namen `mindcraft`); auf `127.0.0.1` gebunden sieht ihn nur
der Host — und Port 8080 auf dem Host ist **qBittorrent**, nicht der MindServer. Wie es eingerichtet
wird: `docs/LEHREN.md`, 29.10.2026.

Steuern ist eine Positivliste (`ERLAUBT`): starten, stoppen, neu starten. Dazu kommen zwei Wege IN das
Spiel: `inventar()` liest, was ein Bot bei sich hat, und `aufgabe()` gibt ihm über den MindServer eine
Aufgabe. Alles andere bleibt gesperrt.

Jede Aktion landet in `data/minecraft_bot/aktionen.jsonl` (Zeit, Aktion, Ergebnis, Container, höchstens
ein kurzer Grund; nie Logtext). Inventarnamen und Botnamen sind fremder Text und werden nur angezeigt,
nie ausgewertet; die Positionszahlen kommen aus dem MindServer.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime

from kern import config
from kern.dienste import hphome, mindserver

STANDARD_CONTAINER = "friday-mc-mindcraft-1"
# Der MindServer (Mindcraft) hört im Container auf diesem Port; veröffentlicht wird er über einen Host-Port.
MINDSERVER_PORT = 8080
# So erreicht Friday einen auf dem Host veröffentlichten Port (extra_hosts host-gateway, wie qBittorrent).
MINDSERVER_HOST = "host.docker.internal"
# Die Positivliste. Alles andere wird abgelehnt, ohne den Helfer überhaupt zu fragen.
ERLAUBT = ("start", "stopp", "neustart")
_ALIASE = {
    "start": "start", "starte": "start", "starten": "start", "einschalten": "start", "an": "start",
    "stopp": "stopp", "stop": "stopp", "stoppe": "stopp", "stoppen": "stopp", "anhalten": "stopp",
    "ausschalten": "stopp", "aus": "stopp",
    "neustart": "neustart", "neu starten": "neustart", "neu_starten": "neustart",
    "neustarten": "neustart", "restart": "neustart",
}
_WORT = {"start": "Der Minecraft-Bot wurde gestartet.",
         "stopp": "Der Minecraft-Bot wurde gestoppt.",
         "neustart": "Der Minecraft-Bot wurde neu gestartet."}

LOG_KOPF = "Protokoll des Minecraft-Bots"
INVENTAR_KOPF = "Inventar von"
MAX_LOGZEILEN = 100          # mehr gibt der hphome-Helfer ohnehin nicht her
MAX_LOGZEICHEN = 12000
MAX_BOTEN = 20
MAX_INVENTAR = 80            # so viele verschiedene Arten zeigt das Inventar
MAX_AUFGABE = 400            # länger passt nicht in eine Spiel-Chat-Nachricht
# Der MindServer schickt den Spielzustand im Sekundentakt, nachdem man ihn abonniert hat.
WARTE_ZUSTAND = 1.8
ZEITLIMIT_MIND = 2.5
# So schickt die Web-Oberfläche des MindServers selbst Nachrichten. Wichtig: Der Agent verwirft
# Nachrichten, deren Absender sein eigener Name ist (agent.js, `username === this.name`).
ABSENDER = "ADMIN"

# Steuerzeichen und ANSI-Farben aus `docker logs` entfernen; ein Log ist fremder Text und gehört lesbar,
# aber ohne Formatierungsmüll ins Gespräch.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_STEUERZEICHEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def container() -> str:
    return os.getenv("MINECRAFT_BOT_CONTAINER", "").strip() or STANDARD_CONTAINER


def _container_daten(name: str) -> dict | None:
    """Der Container aus dem hphome-Zustand — dort steht auch die veröffentlichte Port-Zuordnung."""
    z = hphome.zustand_roh()
    if not isinstance(z, dict):
        return None
    for c in z.get("container") or []:
        if isinstance(c, dict) and c.get("name") == name:
            return c
    return None


def _adresse_aus_container(c: dict | None) -> str:
    """Die MindServer-Adresse aus der Konfiguration des Bot-Containers: der Host-Port, der auf
    `MINDSERVER_PORT` im Container zeigt (bei `host_netz` der Port selbst). Ohne diese Zuordnung leer —
    ein fremder Port wäre geraten."""
    if not isinstance(c, dict):
        return ""
    if c.get("host_netz") is True:
        port = MINDSERVER_PORT
    else:
        port = None
        for p in c.get("ports") or []:
            if isinstance(p, dict) and p.get("container") == MINDSERVER_PORT:
                port = p.get("host")
                break
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        return ""
    return f"http://{MINDSERVER_HOST}:{port}"


def mindserver_url() -> str:
    """Die Adresse des MindServers. `MINECRAFT_BOT_URL` gewinnt; sonst wird sie aus der veröffentlichten
    Port-Zuordnung des Bot-Containers gelesen, die der hphome-Helfer mit dem Zustand liefert."""
    fest = os.getenv("MINECRAFT_BOT_URL", "").strip().rstrip("/")
    if fest:
        return fest
    return _adresse_aus_container(_container_daten(container()))


def _kurz(text, n: int = 300) -> str:
    return " ".join(str(text or "").split())[:n]


def _dauer(sekunden: float) -> str:
    s = max(0, int(sekunden))
    if s >= 86400:
        tage = s // 86400
        return f"{tage} Tag" if tage == 1 else f"{tage} Tagen"
    if s >= 3600:
        stunden = s // 3600
        return f"{stunden} Stunde" if stunden == 1 else f"{stunden} Stunden"
    if s >= 60:
        minuten = s // 60
        return f"{minuten} Minute" if minuten == 1 else f"{minuten} Minuten"
    return "wenigen Sekunden"


def _name(wert) -> str:
    return _STEUERZEICHEN.sub("", str(wert or "")).strip()[:40]


def _wahr(wert):
    return wert if isinstance(wert, bool) else None


def _zahl(wert):
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    return wert


def _komma(wert) -> str:
    """Zahl fürs Gespräch: ganze Zahlen ohne Nachkomma, sonst mit Komma (deutsch)."""
    zahl = float(wert)
    return str(int(zahl)) if zahl.is_integer() else f"{zahl:.1f}".replace(".", ",")


# --------------------------------------------------------------------------------------------
# Die Verbindung — in Tests ersetzt
# --------------------------------------------------------------------------------------------

async def _verbinden(url: str):
    """Eine Verbindung zum MindServer öffnen (in Tests durch eine Attrappe ersetzt)."""
    return await mindserver.verbinden(url, ZEITLIMIT_MIND)


async def _vom_mindserver() -> tuple[list[dict], dict, str]:
    """(Boten, Spielzustand je Botname, Fehlertext) — ein Verbindungsaufbau je Auskunft.

    Ohne Adresse: ([], {}, "") — der Aufrufer sagt dann, dass die Adresse fehlt."""
    url = mindserver_url()
    if not url:
        return [], {}, ""
    verbindung = None
    try:
        verbindung = await _verbinden(url)
        boten = await _boten_lesen(verbindung)
        zustaende: dict = {}
        if any(b["im_spiel"] is True for b in boten):
            await verbindung.senden("listen-to-agents")
            zustaende = await _zustaende_lesen(verbindung)
        return boten, zustaende, ""
    except mindserver.Fehler as exc:
        return [], {}, str(exc)
    finally:
        if verbindung is not None:
            await verbindung.schliessen()


async def _boten_lesen(verbindung) -> list[dict]:
    """Auf `agents-status` warten — der MindServer schickt es direkt nach dem Verbinden."""
    while True:
        name, daten = await verbindung.empfangen(ZEITLIMIT_MIND)
        if name == "agents-status":
            return _boten(daten[0] if daten else [])


async def _zustaende_lesen(verbindung) -> dict:
    """Einen `state-update` abwarten. Bleibt er aus, ist das kein Fehler: Dann fehlt nur der Live-Ort."""
    try:
        while True:
            name, daten = await verbindung.empfangen(WARTE_ZUSTAND)
            if name == "state-update" and daten and isinstance(daten[0], dict):
                return daten[0]
    except mindserver.Fehler:
        return {}


def _boten(roh) -> list[dict]:
    """Die Antwort des MindServers auf bekannte Felder zurückschneiden. Erwartet wird die Liste aus
    `agents-status` (`[{name, in_game, ...}]`); ein Objekt `{name: {...}}` wird ebenso verstanden."""
    eintraege: list = []
    if isinstance(roh, dict):
        if isinstance(roh.get("agents"), list):
            eintraege = roh["agents"]
        else:
            for name, info in roh.items():
                if name in ("ok", "message", "status"):
                    continue
                if isinstance(info, dict):
                    eintraege.append({**info, "name": info.get("name") or info.get("username") or name})
                else:
                    eintraege.append({"name": name, "in_game": info})
    elif isinstance(roh, list):
        eintraege = roh
    heraus, gesehen = [], set()
    for e in eintraege:
        if isinstance(e, str):
            e = {"name": e}
        if not isinstance(e, dict):
            continue
        n = _name(e.get("name") or e.get("username") or e.get("agent"))
        if not n or n.casefold() in gesehen:
            continue
        gesehen.add(n.casefold())
        heraus.append({"name": n, "im_spiel": _wahr(e.get("in_game", e.get("ingame"))),
                       "bereit": _wahr(e.get("idle", e.get("bereit")))})
    return heraus[:MAX_BOTEN]


def _waehlen(boten: list[dict], name: str = "") -> tuple[dict | None, str]:
    """Den gemeinten Bot finden: der genannte, sonst der einzige. Bei mehreren fragt Friday nach."""
    name = _name(name)
    if name:
        for b in boten:
            if b["name"].casefold() == name.casefold():
                return b, ""
        return None, f"Den Bot „{name}“ meldet der MindServer nicht."
    if len(boten) == 1:
        return boten[0], ""
    if not boten:
        return None, "Der MindServer meldet keinen laufenden Bot."
    return None, ("Der MindServer meldet mehrere Bots (" + ", ".join(f"„{b['name']}“" for b in boten) +
                  "). Welchen meinst du?")


def _bote_text(b: dict) -> str:
    teile = [f"„{b['name']}“"]
    if b["im_spiel"] is True:
        teile.append("im Spiel")
    elif b["im_spiel"] is False:
        teile.append("nicht im Spiel")
    if b["bereit"] is True:
        teile.append("wartet")
    elif b["bereit"] is False:
        teile.append("beschäftigt")
    return " ".join(teile)


def _position(roh: dict) -> tuple:
    """Koordinaten aus `position`/`pos` (Objekt oder Liste) oder den Feldern x/y/z. Sonst (None, None, None)."""
    pos = roh.get("position") or roh.get("pos")
    if isinstance(pos, dict):
        werte = (pos.get("x"), pos.get("y"), pos.get("z"))
    elif isinstance(pos, (list, tuple)) and len(pos) >= 3:
        werte = tuple(pos[:3])
    else:
        werte = (roh.get("x"), roh.get("y"), roh.get("z"))
    if all(_zahl(w) is not None for w in werte):
        return werte
    return (None, None, None)


def _spiel_text(zustand) -> str:
    """Was der MindServer über einen Bot im Spiel meldet (Form vom 29.10.2026: `gameplay`, `action`).

    Nur bekannte Felder, keine freien Texte — außer der laufenden Aktion, die der Bot selbst vergibt
    (`autopilot:…`, feste Namen) und die hier gekürzt und von Steuerzeichen befreit wird. Leerer Text,
    wenn nichts davon da ist."""
    if not isinstance(zustand, dict):
        return ""
    spiel = zustand.get("gameplay") if isinstance(zustand.get("gameplay"), dict) else zustand
    teile = []
    x, y, z = _position(spiel)
    if x is not None:
        teile.append(f"bei {_komma(x)}/{_komma(y)}/{_komma(z)}")
    dimension = _kurz(spiel.get("dimension") or spiel.get("welt"), 40)
    if dimension:
        teile.append(f"in {dimension}")
    leben = _zahl(spiel.get("health", spiel.get("leben", spiel.get("hp"))))
    if leben is not None:
        maximum = _zahl(spiel.get("health_max", spiel.get("leben_max"))) or 20
        teile.append(f"Leben {_komma(leben)}/{_komma(maximum)}")
    hunger = _zahl(spiel.get("hunger", spiel.get("food", spiel.get("nahrung"))))
    if hunger is not None:
        teile.append(f"Hunger {_komma(hunger)}/20")
    aktion = zustand.get("action") if isinstance(zustand.get("action"), dict) else {}
    was = _kurz(_STEUERZEICHEN.sub("", str(aktion.get("current") or "")), 60)
    if was:
        teile.append(f"tut gerade: {was}")
    return ", ".join(teile)


# --------------------------------------------------------------------------------------------
# Auskünfte
# --------------------------------------------------------------------------------------------

async def zustand() -> str:
    """Was der Bot tut: Containerzustand vom hphome-Helfer, Spielzustand vom MindServer (wenn eingerichtet)."""
    z = hphome.zustand_roh()
    if z is None:
        return ("Ich komme gerade nicht an den Minecraft-Bot: Der Helfer auf hphome meldet sich nicht. "
                "Ohne ihn kann ich weder den Container noch den MindServer sehen.")
    if z.get("container_ok") is False:
        return "Docker auf hphome antwortet nicht — zum Minecraft-Bot kann ich gerade nichts sagen."
    name = container()
    c = next((x for x in z.get("container") or [] if x.get("name") == name), None)
    teile = []
    if c is None:
        teile.append(f"Den Container „{name}“ kenne ich auf hphome nicht.")
    elif c.get("zustand") == "running":
        seit = c.get("seit")
        laufzeit = f" seit {_dauer(time.time() - seit)}" if isinstance(seit, (int, float)) and seit > 0 else ""
        teile.append(f"Der Minecraft-Bot läuft{laufzeit}.")
        if c.get("gesundheit") == "unhealthy":
            teile.append("Docker meldet ihn als nicht gesund.")
    else:
        teile.append(f"Der Minecraft-Bot ist gestoppt ({(c.get('zustand') or 'unbekannt')}).")
    if not mindserver_url():
        teile.append("Was er im Spiel tut, kann ich nicht sehen — dafür fehlt mir die Adresse seines "
                     "MindServers (MINECRAFT_BOT_URL in .env oder eine veröffentlichte MindServer-Port-"
                     "Zuordnung am Container).")
        return " ".join(teile)
    boten, zustaende, fehler = await _vom_mindserver()
    if fehler:
        teile.append(fehler)
    elif boten:
        teile.append("Der MindServer meldet: " + ", ".join(_bote_text(b) for b in boten) + ".")
        # Live dazusehen, wo der Bot steht — statt es aus Logzeilen zu raten.
        for b in boten:
            if b["im_spiel"] is not True:
                continue
            live = _spiel_text(zustaende.get(b["name"]))
            if live:
                teile.append(f"„{b['name']}“ steht {live}.")
    else:
        teile.append("Der MindServer meldet keinen laufenden Bot.")
    return " ".join(teile)


def _saeubern(text) -> list[str]:
    text = _STEUERZEICHEN.sub("", _ANSI.sub("", str(text or "")))
    return [z.rstrip() for z in text.splitlines() if z.strip()]


async def logs(zeilen: int = 40) -> str:
    """Die letzten Zeilen aus dem Bot-Protokoll — über den hphome-Helfer, nicht über eine neue Docker-Tür."""
    try:
        n = int(zeilen)
    except (TypeError, ValueError):
        n = 40
    n = max(1, min(n, MAX_LOGZEILEN))
    try:
        antwort = await asyncio.to_thread(hphome.logs, container())
    except hphome.HphomeFehler as exc:
        return f"Ich komme gerade nicht an das Bot-Protokoll: {exc}"
    except Exception as exc:                                  # noqa: BLE001 — ein Lesefehler ist eine Auskunft
        return f"Ich konnte das Bot-Protokoll nicht holen ({type(exc).__name__})."
    if not antwort.get("ok"):
        return f"Das Bot-Protokoll kam nicht: {_kurz(antwort.get('text'))}"
    liste = _saeubern(antwort.get("text"))
    if liste == ["(keine Zeilen)"]:          # so meldet der hphome-Helfer ein leeres Protokoll
        liste = []
    liste = liste[-n:]
    text = "\n".join(liste)
    if len(text) > MAX_LOGZEICHEN:
        text = text[-MAX_LOGZEICHEN:]
        text = text.split("\n", 1)[-1] if "\n" in text else text
    if not text:
        return "Das Bot-Protokoll ist gerade leer."
    return f"{LOG_KOPF} (letzte {len(liste)} Zeilen):\n{text}"


def _stapel_liste(liste: list) -> list[dict]:
    """Gegenstände auf Name+Anzahl zurückschneiden und gleiche Namen zusammenfassen.
    Die Namen sind fremder Text (ein Server kann Gegenstände umbenennen), darum nur Anzeige."""
    zusammen: dict[str, dict] = {}
    for e in liste:
        if not isinstance(e, dict):
            continue
        name = _name(e.get("displayName") or e.get("name") or e.get("id") or e.get("item") or e.get("type"))
        if not name:
            continue
        anzahl = _zahl(e.get("count", e.get("anzahl", e.get("quantity", e.get("amount")))))
        anzahl = 1 if anzahl is None else int(max(0, anzahl))
        if anzahl == 0:                       # leerer Platz im Inventar
            continue
        schluessel = name.casefold()
        if schluessel in zusammen:
            zusammen[schluessel]["anzahl"] += anzahl
        else:
            zusammen[schluessel] = {"name": name, "anzahl": anzahl}
    return list(zusammen.values())


def _stapel(roh) -> list[dict] | None:
    """Das Inventar aus dem Spielzustand. Die Form vom 29.10.2026 ist
    `{"counts": {name: anzahl}, "equipment": {...}}` — eine bloße Zählung, keine Stapel.
    Eine Liste von Stapeln (andere Fassungen) wird ebenso verstanden. Unbekanntes Format: None."""
    if not isinstance(roh, dict):
        return None
    zaehlung = roh.get("counts")
    if isinstance(zaehlung, dict):
        zusammen: dict[str, dict] = {}
        for name, anzahl in zaehlung.items():
            n = _name(name)
            a = _zahl(anzahl)
            if not n or a is None or a <= 0:
                continue
            schluessel = n.casefold()
            if schluessel in zusammen:
                zusammen[schluessel]["anzahl"] += int(a)
            else:
                zusammen[schluessel] = {"name": n, "anzahl": int(a)}
        return sorted(zusammen.values(), key=lambda s: (-s["anzahl"], s["name"].casefold()))
    for schluessel in ("items", "inventar", "slots", "inventory"):
        if isinstance(roh.get(schluessel), list):
            return _stapel_liste(roh[schluessel])
    return None


def _in_der_hand(roh) -> str:
    """Der Gegenstand in der Hand aus `inventory.equipment.mainHand` (oder einem Feld `mainHand`)."""
    if not isinstance(roh, dict):
        return ""
    ausruestung = roh.get("equipment") if isinstance(roh.get("equipment"), dict) else roh
    hand = ausruestung.get("mainHand") or ausruestung.get("mainhand")
    return _name(hand) if hand else ""


async def inventar(bot: str = "") -> str:
    """Was ein Bot bei sich hat — aus dem Spielzustand des MindServers (`state-update`)."""
    if not mindserver_url():
        return ("Ich kann das Inventar nicht lesen — dafür fehlt mir die Adresse des MindServers "
                "(MINECRAFT_BOT_URL in .env oder eine veröffentlichte MindServer-Port-Zuordnung am Container).")
    boten, zustaende, fehler = await _vom_mindserver()
    if fehler:
        return f"Ich kann das Inventar nicht lesen: {fehler}"
    b, grund = _waehlen(boten, bot)
    if b is None:
        return f"Ich kann das Inventar nicht lesen: {grund}"
    if b["im_spiel"] is False:
        return f"Ich kann das Inventar nicht lesen: „{b['name']}“ ist nicht im Spiel."
    roh = (zustaende.get(b["name"]) or {}).get("inventory")
    stapel = _stapel(roh)
    if stapel is None:
        return "Ich kann das Inventar nicht lesen — der MindServer antwortet in einem unerwarteten Format."
    kopf = f"{INVENTAR_KOPF} „{b['name']}“"
    if not stapel:
        return f"{kopf}: leer."
    zeilen = [f"- {s['name']} × {s['anzahl']}" for s in stapel[:MAX_INVENTAR]]
    rest = len(stapel) - len(zeilen)
    if rest > 0:
        zeilen.append(f"… und {rest} weitere Arten")
    text = f"{kopf} ({len(stapel)} {'Art' if len(stapel) == 1 else 'Arten'}):\n" + "\n".join(zeilen)
    hand = _in_der_hand(roh)
    if hand:
        text += f"\nIn der Hand: {hand}."
    return text


def _protokoll(aktion: str, ok: bool, grund: str = "", bot: str = "") -> None:
    eintrag = {"zeit": datetime.now().astimezone().isoformat(timespec="seconds"), "aktion": aktion,
               "ok": bool(ok), "container": container()}
    if bot:
        eintrag["bot"] = _name(bot)
    if grund:
        eintrag["grund"] = _kurz(grund, 200)
    ordner = config.daten_ordner() / "minecraft_bot"
    try:
        ordner.mkdir(parents=True, exist_ok=True)
        with open(ordner / "aktionen.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    except OSError:
        pass


async def steuern(aktion: str) -> str:
    a = _ALIASE.get(" ".join(str(aktion or "").lower().split()))
    if a is None:
        return ("NICHT ERLEDIGT: Das kann ich mit dem Minecraft-Bot nicht machen. "
                "Erlaubt sind: starten, stoppen, neu starten.")
    try:
        antwort = await asyncio.to_thread(hphome.container_aktion, container(), a)
    except hphome.HphomeFehler as exc:
        _protokoll(a, False, str(exc))
        return f"NICHT ERLEDIGT: {exc}"
    except Exception as exc:                                  # noqa: BLE001 — der Helfer kann immer ausfallen
        _protokoll(a, False, type(exc).__name__)
        return f"NICHT ERLEDIGT: Das hat nicht geklappt ({type(exc).__name__})."
    ok = bool(antwort.get("ok"))
    _protokoll(a, ok, "" if ok else antwort.get("text") or "")
    if not ok:
        return f"NICHT ERLEDIGT: {_kurz(antwort.get('text')) or 'Der Helfer hat nicht reagiert.'}"
    return _WORT[a]


# Die Modelle, zwischen denen der Bot umgestellt werden kann. Gemini ist der bisherige Stand; DeepSeek läuft
# über Eikes vorhandenen Schlüssel (der Helfer liest ihn auf dem Host, hierher kommt er nie zurück).
MODELLE = {"gemini": "Gemini", "deepseek": "DeepSeek"}


def botmodell() -> str:
    """`deepseek`, `gemini` oder "" — was der Bot laut Helfer gerade benutzt (nur zur Anzeige)."""
    z = hphome.zustand_roh()
    eintrag = z.get("mindcraft") if isinstance(z, dict) else None
    modell = eintrag.get("modell") if isinstance(eintrag, dict) else None
    return modell if modell in MODELLE else ""


async def botmodell_setzen(modell: str) -> str:
    """Stellt den Bot auf Gemini oder DeepSeek um — über den hphome-Helfer, der Profil und Schlüssel schreibt
    und den Container neu startet. Der Bot ist dabei kurz aus dem Spiel."""
    m = str(modell or "").strip().lower()
    if m not in MODELLE:
        return "NICHT ERLEDIGT: Das Modell kenne ich nicht — erlaubt sind Gemini und DeepSeek."
    try:
        antwort = await asyncio.to_thread(hphome.bot_modell, m)
    except hphome.HphomeFehler as exc:
        _protokoll("modell", False, str(exc))
        return f"NICHT ERLEDIGT: {exc}"
    except Exception as exc:                                  # noqa: BLE001 — der Helfer kann immer ausfallen
        _protokoll("modell", False, type(exc).__name__)
        return f"NICHT ERLEDIGT: Das hat nicht geklappt ({type(exc).__name__})."
    ok = bool(antwort.get("ok"))
    _protokoll("modell", ok, "" if ok else antwort.get("text") or "")
    if not ok:
        return f"NICHT ERLEDIGT: {_kurz(antwort.get('text')) or 'Der Helfer hat nicht reagiert.'}"
    return _kurz(antwort.get("text"), 300) or f"Der Bot läuft jetzt auf {MODELLE[m]}."


async def aufgabe(text: str, bot: str = "") -> str:
    """Einem Bot im Spiel eine Aufgabe geben: eine Nachricht an seinen MindServer-Agenten."""
    t = " ".join(_STEUERZEICHEN.sub("", str(text or "")).split())
    if not t:
        return "NICHT ERLEDIGT: Die Aufgabe ist leer — ich habe dem Bot nichts gesagt."
    if len(t) > MAX_AUFGABE:
        return (f"NICHT ERLEDIGT: Die Aufgabe ist zu lang ({len(t)} statt höchstens {MAX_AUFGABE} Zeichen) — "
                "fass sie kürzer.")
    url = mindserver_url()
    if not url:
        return ("NICHT ERLEDIGT: Dafür fehlt mir die Adresse des MindServers (MINECRAFT_BOT_URL in .env oder "
                "eine veröffentlichte MindServer-Port-Zuordnung am Container) — ich kann dem Bot nichts sagen.")
    verbindung = None
    try:
        verbindung = await _verbinden(url)
        boten = await _boten_lesen(verbindung)
        b, grund = _waehlen(boten, bot)
        if b is None:
            return f"NICHT ERLEDIGT: {grund}"
        if b["im_spiel"] is False:
            return f"NICHT ERLEDIGT: „{b['name']}“ ist nicht im Spiel — der Auftrag käme nicht an."
        await verbindung.senden("send-message", b["name"], {"from": ABSENDER, "message": t})
    except mindserver.Fehler as exc:
        _protokoll("aufgabe", False, str(exc), _name(bot))
        return f"NICHT ERLEDIGT: {exc}"
    finally:
        if verbindung is not None:
            await verbindung.schliessen()
    _protokoll("aufgabe", True, t, b["name"])
    # Der MindServer bestätigt eine Chat-Nachricht nicht: geschickt heißt nicht ausgeführt.
    return f"Auftrag an „{b['name']}“ geschickt: „{t}“"
