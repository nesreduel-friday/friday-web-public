"""Morgenbriefing — der Tag in einer Benachrichtigung.

Aus docs/STRUKTUR.md, Baustein 4: „Morgenbriefing (Stundenplan, Hausaufgaben,
Wetter, erste Verbindung)“. Gebaut am 14.09.2026, mit drei bewussten Abwegen:

- **Ohne Sprachmodell zusammengesetzt.** Jede Zeile kommt direkt aus einer
  Quelle. Nichts kann erfunden werden (vgl. Lehrkraft „Peters“ und Raum
  „B 1.15“ vom 14.09.), und es kostet kein Gemini-Kontingent.
- **Keine Hausaufgaben.** Der Schulmanager kennt nur den Tag, an dem eine
  Aufgabe GESTELLT wurde, keinen Abgabetermin. „Heute fällig“ wäre geraten.
- **Keine Verbindung.** Ohne Wissen, wann Eike los muss, wäre das eine Zeile
  ohne Nutzen. (Damals kam auch keine Echtzeit; seit 15.09.2026 liefert der
  GVH sie für zeitnahe Fahrten — um 6:30 für eine spätere Fahrt oft noch nicht.)

**Eine Einstellung, keine Freigabe** (Eikes Entscheidung, 14.09.2026): Das
Briefing holt nichts, was nicht schon freigegeben ist — Wetter, Stundenplan und
Termine kommen nur hinein, wenn ihre Konnektoren an sind. Ob es kommt und
wann, steht in `data/einstellungen.json` und lässt sich in den Einstellungen
schalten oder per Zuruf über das Werkzeug `morgenbriefing`. Standard: aus.

Einmal pro Tag, Stand in `data/briefing.json` (600): ein Neustart um 6:31
schickt kein zweites. War der Server zur Uhrzeit aus, wird bis zu
`NACHHOLEN` später nachgeholt, danach nicht mehr — ein Briefing am Mittag
ist keins.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from datetime import date, datetime, time, timedelta

from kern import config, erinnerungen, konnektoren
from kern.dienste import benachrichtigungen, icloud, push, schulmanager, wetter
from kern.gehirn.persona import WOCHENTAGE

log = logging.getLogger("friday.briefing")

TAKT_S = 60
NACHHOLEN = timedelta(hours=2)
NEUER_VERSUCH = timedelta(minutes=10)
MAX_VERSUCHE = 3
QUELLE_ZEITLIMIT = 25
STANDARD_UHRZEIT = time(6, 30)
MAX_EINTRAEGE = 3

_sperre = threading.Lock()


# ---------- Dateien ----------

def _schreiben(ziel, daten: dict) -> None:
    """Atomar und 600, wie alle Zustandsdateien in data/."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(daten, f)
    os.chmod(neu, 0o600)
    os.replace(neu, ziel)


def _lesen(ziel) -> dict:
    try:
        d = json.loads(ziel.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------- Einstellung ----------

def _einstellungsdatei():
    return config.daten_ordner() / "einstellungen.json"


def uhrzeit_deuten(text: str | None) -> time | None:
    """„06:30“, „6:30“ oder „6.30“ → 06:30. Alles andere → None."""
    treffer = re.fullmatch(r"(\d{1,2})[:.](\d{2})", (text or "").strip())
    if not treffer or int(treffer[1]) > 23 or int(treffer[2]) > 59:
        return None
    return time(int(treffer[1]), int(treffer[2]))


def ist_an() -> bool:
    return _lesen(_einstellungsdatei()).get("morgenbriefing_an") is True


def uhrzeit() -> time:
    """Aus den Einstellungen, sonst `BRIEFING_UHRZEIT`, sonst 06:30."""
    return (uhrzeit_deuten(_lesen(_einstellungsdatei()).get("morgenbriefing_uhrzeit"))
            or uhrzeit_deuten(os.getenv("BRIEFING_UHRZEIT")) or STANDARD_UHRZEIT)


def einstellen(an: bool | None = None, uhrzeit_text: str | None = None) -> None:
    """Schalten und/oder Uhrzeit setzen. Eine unlesbare Uhrzeit ändert NICHTS."""
    neue_zeit = None
    if uhrzeit_text is not None:
        neue_zeit = uhrzeit_deuten(uhrzeit_text)
        if neue_zeit is None:
            raise ValueError("Uhrzeit bitte als HH:MM")
    with _sperre:
        d = _lesen(_einstellungsdatei())
        if an is not None:
            d["morgenbriefing_an"] = bool(an)
        if neue_zeit is not None:
            d["morgenbriefing_uhrzeit"] = f"{neue_zeit:%H:%M}"
        _schreiben(_einstellungsdatei(), d)


STANDARD_ABENDBLICK_UHRZEIT = time(20, 0)


def abendblick_an() -> bool:
    return _lesen(_einstellungsdatei()).get("abendblick_an") is True


def abendblick_uhrzeit() -> time:
    return (uhrzeit_deuten(_lesen(_einstellungsdatei()).get("abendblick_uhrzeit"))
            or uhrzeit_deuten(os.getenv("ABENDBLICK_UHRZEIT")) or STANDARD_ABENDBLICK_UHRZEIT)


def abendblick_einstellen(an: bool | None = None, uhrzeit_text: str | None = None) -> None:
    neue_zeit = None
    if uhrzeit_text is not None:
        neue_zeit = uhrzeit_deuten(uhrzeit_text)
        if neue_zeit is None:
            raise ValueError("Uhrzeit bitte als HH:MM")
    with _sperre:
        d = _lesen(_einstellungsdatei())
        if an is not None:
            d["abendblick_an"] = bool(an)
        if neue_zeit is not None:
            d["abendblick_uhrzeit"] = f"{neue_zeit:%H:%M}"
        _schreiben(_einstellungsdatei(), d)


def stand_text() -> str:
    """Für Friday: was gilt, und was dem Briefing gerade fehlt."""
    teile = [f"Das Morgenbriefing ist {'an' if ist_an() else 'aus'}, Uhrzeit {uhrzeit():%H:%M}."]
    if abendblick_an():
        teile.append(f"Der Abendblick auf morgen ist an, Uhrzeit {abendblick_uhrzeit():%H:%M}.")
    if ist_an() or abendblick_an():
        if not push.geraete():
            teile.append("Achtung: Es ist kein Gerät für Benachrichtigungen angemeldet — so kommt nichts an.")
        if not konnektoren.benutzbar("wetter"):
            teile.append("Das Wetter fehlt darin, solange der Wetter-Konnektor auf der Freigaben-Seite aus ist.")
    return " ".join(teile)


def _lokal(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(config.jetzt().tzinfo)


# ---------- Die Zeilen ----------

async def wetter_zeile(tag: date) -> str | None:
    if not konnektoren.benutzbar("wetter"):
        return None
    v = await wetter.vorhersage(wetter.heimatort(), (tag - config.jetzt().date()).days + 1)
    t = next((t for t in v["tage"] if t["datum"] == tag.isoformat()), None)
    if not t:
        return "Wetter: keine Vorhersage für heute."
    return f"Wetter: {wetter.kurz(t)}"


def _fach(stunde: dict) -> str:
    from kern.werkzeuge.schule import _fach
    quelle = stunde.get("actualLesson") or (stunde.get("originalLessons") or [{}])[0] or {}
    return _fach(quelle)


def _nummer(stunde: dict) -> int:
    """Der Schulmanager liefert die Stundennummer als TEXT („1“, „10“) — als Text
    sortiert käme die 10. Stunde vor der 2."""
    try:
        return int((stunde.get("classHour") or {}).get("number"))
    except (TypeError, ValueError):
        return 99


def _spanne(stunden: list[dict]) -> str:
    """„3. Stunde“ oder „1.–6. Stunde“ — von der ersten bis zur letzten der Liste."""
    von = (stunden[0].get("classHour") or {}).get("number", "?")
    bis = (stunden[-1].get("classHour") or {}).get("number", "?")
    return f"{von}. Stunde" if von == bis else f"{von}.–{bis}. Stunde"


async def schule_zeile(tag: date) -> str | None:
    if not konnektoren.benutzbar("schulmanager"):
        return None
    montag = tag - timedelta(days=tag.weekday())
    stunden = await schulmanager.stundenplan(montag, montag + timedelta(days=6))
    eigene = sorted((s for s in stunden if s.get("date") == tag.isoformat()), key=_nummer)
    if not eigene:
        if tag.weekday() >= 5:
            return None                                  # Wochenende: kein Wort nötig
        # Dieselbe Unterscheidung wie im Werkzeug (`schule.tages_text`): eine
        # leere Woche ist KEINE Aussage über den Tag.
        if not stunden:
            return "Schule: keine Stundenplandaten — das heißt nicht, dass frei ist."
        return "Schule: kein Unterricht eingetragen."
    findet = [s for s in eigene if s.get("type") != "cancelledLesson"]
    if not findet:
        # NICHT „alle Stunden fallen aus": Am 15.09.2026 standen nur die 3. und 4.
        # Stunde im Plan (beide Ausfall), an den anderen Tagen 6–8. Belegt ist nur,
        # dass DIESE ausfallen — ob der Rest frei ist, sagt der Schulmanager nicht.
        faecher = ", ".join(dict.fromkeys(_fach(s) for s in eigene))
        return f"Schule: eingetragen ist nur die {_spanne(eigene)} ({faecher}) — fällt aus."
    erste, letzte = findet[0].get("classHour") or {}, findet[-1].get("classHour") or {}
    zeile = f"Schule: {_spanne(findet)}"
    # Nur gelieferte Zeiten — fehlt eine, steht keine da, statt einer ausgedachten.
    if erste.get("from") and letzte.get("until"):
        zeile += f", {str(erste['from'])[:5]}–{str(letzte['until'])[:5]}"
    if ausfall := [s for s in eigene if s.get("type") == "cancelledLesson"]:
        zeile += "; fällt aus: " + ", ".join(dict.fromkeys(_fach(s) for s in ausfall))
    if vertretung := [s for s in eigene if s.get("type") == "substitution"]:
        zeile += f"; {len(vertretung)} Vertretung" + ("en" if len(vertretung) > 1 else "")
    return zeile


def _aufzaehlen(teile: list[str]) -> str:
    rest = len(teile) - MAX_EINTRAEGE
    return ", ".join(teile[:MAX_EINTRAEGE]) + (f" (+{rest} weitere)" if rest > 0 else "")


async def kalender_zeile(tag: date) -> str | None:
    if not konnektoren.benutzbar("icloud_kalender"):
        return None
    zone = config.jetzt().tzinfo
    termine = await asyncio.to_thread(icloud.termine, datetime.combine(tag, time.min, zone),
                                      datetime.combine(tag, time.max, zone))
    if not termine:
        return "Termine: keine."
    return "Termine: " + _aufzaehlen([t["titel"] if t["ganztags"] else f"{_lokal(t['beginn']):%H:%M} {t['titel']}"
                                      for t in termine])


async def erinnerungen_zeile(tag: date) -> str | None:
    heute = [e for e in erinnerungen.standard().offene(50) if _lokal(e["faellig"]).date() == tag]
    if not heute:
        return None
    return "Erinnerungen: " + _aufzaehlen([f"{_lokal(e['faellig']):%H:%M} {e['titel']}" for e in heute])


async def whatsapp_zeile(tag: date) -> str | None:
    """Was in den freigegebenen Chats aufgelaufen ist — NUR Zahlen und Namen.

    Bewusst ohne Inhalt: Das Briefing geht als Push-Nachricht raus und steht auf
    dem Sperrbildschirm. Fremde Chattexte haben dort nichts zu suchen. Wer mehr
    wissen will, fragt Friday — dann greift die Freigabe je Chat wie sonst auch.
    """
    if not konnektoren.benutzbar("whatsapp"):
        return None
    from kern.dienste import whatsapp
    neu = whatsapp.neues()
    if not neu:
        return None
    teile = [f"{n['anzahl']} von {n['name']}" for n in sorted(neu, key=lambda n: -n["anzahl"])[:4]]
    return "WhatsApp: " + _aufzaehlen(teile) + " ungelesen."


async def _sicher(was: str, arbeit) -> str | None:
    """Eine Quelle, die ausfällt, darf das Briefing nicht verhindern."""
    try:
        return await asyncio.wait_for(arbeit, QUELLE_ZEITLIMIT)
    except wetter.WetterFehler as exc:
        return f"{was}: {exc}"
    except Exception as exc:
        log.warning("Briefing: %s nicht abrufbar (%s)", was, type(exc).__name__)
        return f"{was}: gerade nicht abrufbar."


async def briefing(tag: date) -> tuple[str, str]:
    """Überschrift und Text. Die Quellen laufen gleichzeitig."""
    zeilen = await asyncio.gather(_sicher("Wetter", wetter_zeile(tag)), _sicher("Schule", schule_zeile(tag)),
                                  _sicher("Termine", kalender_zeile(tag)),
                                  _sicher("Erinnerungen", erinnerungen_zeile(tag)),
                                  _sicher("WhatsApp", whatsapp_zeile(tag)))
    text = "\n".join(z for z in zeilen if z) or "Heute steht nichts an — oder nichts davon ist freigegeben."
    return f"Guten Morgen — {WOCHENTAGE[tag.weekday()]}, {tag:%d.%m.}", text


async def abendblick(morgen: date) -> tuple[str, str]:
    """Überschrift und Text für den Blick auf morgen."""
    zeilen = await asyncio.gather(_sicher("Wetter", wetter_zeile(morgen)), _sicher("Schule", schule_zeile(morgen)),
                                  _sicher("Termine", kalender_zeile(morgen)),
                                  _sicher("Erinnerungen", erinnerungen_zeile(morgen)))
    text = "\n".join(z for z in zeilen if z) or "Morgen steht nichts an — oder nichts davon ist freigegeben."
    return f"Blick auf morgen — {WOCHENTAGE[morgen.weekday()]}, {morgen:%d.%m.}", text


# ---------- Einmal am Tag ----------

def _datei():
    return config.daten_ordner() / "briefing.json"


def _datei_abendblick():
    return config.daten_ordner() / "abendblick.json"


def stand() -> dict:
    return _lesen(_datei())


def abendblick_stand() -> dict:
    return _lesen(_datei_abendblick())


def faellig(jetzt: datetime, s: dict) -> bool:
    beginn = datetime.combine(jetzt.date(), uhrzeit(), jetzt.tzinfo)
    if not beginn <= jetzt < beginn + NACHHOLEN:
        return False
    if s.get("datum") != jetzt.date().isoformat():
        return True                                      # heute noch nichts versucht
    if s.get("geschickt") or s.get("versuche", 0) >= MAX_VERSUCHE:
        return False
    try:                                                 # nach einem Fehlschlag: etwas warten
        return jetzt - datetime.fromisoformat(s["versucht"]) >= NEUER_VERSUCH
    except (KeyError, ValueError):
        return True


def abendblick_faellig(jetzt: datetime, s: dict) -> bool:
    beginn = datetime.combine(jetzt.date(), abendblick_uhrzeit(), jetzt.tzinfo)
    if not beginn <= jetzt < beginn + NACHHOLEN:
        return False
    if s.get("datum") != jetzt.date().isoformat():
        return True
    if s.get("geschickt") or s.get("versuche", 0) >= MAX_VERSUCHE:
        return False
    try:
        return jetzt - datetime.fromisoformat(s["versucht"]) >= NEUER_VERSUCH
    except (KeyError, ValueError):
        return True


async def einmal(jetzt: datetime | None = None) -> str:
    """Schickt das Briefing, wenn es dran ist. Meldet, was geschehen ist."""
    jetzt = jetzt or config.jetzt()
    if not ist_an():
        return "Das Morgenbriefing ist in den Einstellungen aus."
    s = stand()
    if not faellig(jetzt, s):
        return "Gerade nicht fällig."
    if not push.geraete():
        return "Kein Gerät für Benachrichtigungen angemeldet."
    heute = jetzt.date().isoformat()
    titel, text = await briefing(jetzt.date())
    ergebnis = await asyncio.to_thread(benachrichtigungen.senden, "briefing", titel, text,
                                       "/", f"briefing-{heute}")
    versuche = (s.get("versuche", 0) if s.get("datum") == heute else 0) + 1
    _schreiben(_datei(), {"datum": heute, "geschickt": bool(ergebnis.get("gesendet")), "versuche": versuche,
                          "versucht": jetzt.isoformat(timespec="seconds")})
    if ergebnis.get("gesendet"):
        log.info("Morgenbriefing geschickt")
        return "Morgenbriefing geschickt."
    log.warning("Morgenbriefing nicht zugestellt (Versuch %s): %s", versuche, ergebnis.get("grund"))
    return f"Nicht zugestellt: {ergebnis.get('grund') or 'ohne Grund'}"


async def abendblick_einmal(jetzt: datetime | None = None) -> str:
    """Schickt den Abendblick auf morgen, wenn er dran ist."""
    jetzt = jetzt or config.jetzt()
    if not abendblick_an():
        return "Der Abendblick ist in den Einstellungen aus."
    s = abendblick_stand()
    if not abendblick_faellig(jetzt, s):
        return "Gerade nicht fällig."
    if not push.geraete():
        return "Kein Gerät für Benachrichtigungen angemeldet."
    heute = jetzt.date().isoformat()
    morgen = jetzt.date() + timedelta(days=1)
    titel, text = await abendblick(morgen)
    ergebnis = await asyncio.to_thread(benachrichtigungen.senden, "abendblick", titel, text,
                                       "/", f"abendblick-{heute}")
    versuche = (s.get("versuche", 0) if s.get("datum") == heute else 0) + 1
    _schreiben(_datei_abendblick(), {"datum": heute, "geschickt": bool(ergebnis.get("gesendet")), "versuche": versuche,
                                     "versucht": jetzt.isoformat(timespec="seconds")})
    if ergebnis.get("gesendet"):
        log.info("Abendblick auf morgen geschickt")
        return "Abendblick auf morgen geschickt."
    log.warning("Abendblick nicht zugestellt (Versuch %s): %s", versuche, ergebnis.get("grund"))
    return f"Nicht zugestellt: {ergebnis.get('grund') or 'ohne Grund'}"


async def dauerlauf() -> None:
    await asyncio.sleep(45)
    while True:
        try:
            await einmal()
            await abendblick_einmal()
        except Exception:
            log.exception("Briefing-Dauerlauf gescheitert")
        await asyncio.sleep(TAKT_S)

