"""Der Lauf der Werkbank: liest alle 30 s den Stand laufender Vorhaben und trägt Ergebnisse ein (Spec 2026-10-27-werkbank).

Der Helfer auf hphome schreibt `data/werkbank/<vid>/stand.json` (dazu `diff.patch`, `bericht.md`). Der Ordner ist für Fremdes
beschreibbar, also ist alles darin nicht vertrauenswürdig: nur reguläre Dateien (O_NOFOLLOW, auch für die Ordner), begrenzte
Größe, reines JSON, nur bekannte Felder. Alles Zeitliche kommt aus der DB (`gestartet`), nicht aus dem RAM: Ein Neustart von
Friday verliert keinen Auftrag, und ein hängender wird nach der Frist trotzdem `fehler`.

Gemeldet wird beim Wechsel `laeuft` → `bereit`/`fehler` genau einmal (`Fach.uebergang` ist atomar; derselbe Weg für den
OpenCode-Faden in `starten.py`): `bereit` = Push (Art `werkbank`) + Anliegen, `fehler` = nur Anliegen.

Übernahmen (28.10.2026): Der Knopf (`kern/werkbank/uebernehmen.py`) setzt `uebernommen` + `uebernahme = laeuft`; der Helfer
schreibt das Ergebnis als `uebernahme` (ok/zurueckgerollt/fehler) samt Klartext in `stand.json`. Dieser Lauf trägt es ein
(`ok` bleibt `uebernommen`, sonst zurück auf `bereit`) und meldet es genau einmal per Push. Er ruft den Helfer nie.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import stat
from datetime import datetime

from kern import config
from kern.werkbank import speicher

CLI_BACKENDS = ("claude", "deepseek")             # wie `starten.CLI_BACKENDS` (hier nicht importiert: starten braucht lauf nicht)

log = logging.getLogger("friday.werkbank")

ZEITLIMIT_S = 45 * 60                       # wie im Helfer (claude_helfer.ZEITLIMIT_S): alle Claude-Läufe zusammen
TEST_ZEITLIMIT_S = 20 * 60                  # wie im Helfer; Basis- und Ergebnistests laufen nacheinander
MAX_NACHBESSERUNGEN = 2                     # wie im Helfer: bei roten Tests bis zu 2 weitere Claude-Läufe
FRIST_OHNE_STAND_S = ZEITLIMIT_S + 600
# meldet der Helfer nur noch „laeuft“, ist er hängen geblieben: Basis + ein Testlauf je Claude-Lauf
FRIST_MIT_STAND_S = ZEITLIMIT_S + (2 + MAX_NACHBESSERUNGEN) * TEST_ZEITLIMIT_S + 600
TAKT_S = 30
STAND_MAX = 100_000
BERICHT_MAX = 20_000
DIFF_MAX = 200_000
STAND_ALT_S = 60                            # ein Stand, der so lange vor dem Start liegt, stammt vom vorigen Lauf
STATUS_STAND = ("laeuft", "bereit", "fehler")
UEBERNAHME_ERGEBNIS = ("ok", "zurueckgerollt", "fehler")
FRIST_UEBERNAHME_S = 60 * 60                # zwei Neubauten (je bis 30 min im Helfer) + Gesundheit; danach gilt sie als hängend
UEBERNAHME_ALT_S = 2                        # ein Ergebnis von vor dem Knopfdruck stammt vom vorigen Versuch
_TESTS_OK = re.compile(r"[a-z_]{1,40}")


def _sauber(text: str, grenze: int) -> str:
    from kern.werkzeuge import katalog  # spät: der Katalog zieht viele Module nach
    return katalog.geheimes_schwaerzen(str(text or ""))[:grenze]


def datei_lesen(vid: int, name: str, grenze: int) -> tuple[bytes, bool] | None:
    """`data/werkbank/<vid>/<name>` als Bytes (höchstens `grenze`) samt „gekürzt?“; None bei allem, was keine echte Datei ist."""
    if name not in ("stand.json", "diff.patch", "bericht.md"):
        return None
    fds = []
    try:
        fd = os.open(config.daten_ordner() / "werkbank", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(fd)
        fd = os.open(str(int(vid)), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        fds.append(fd)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        fds.append(fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        roh = b""
        while len(roh) <= grenze:
            stueck = os.read(fd, grenze + 1 - len(roh))
            if not stueck:
                break
            roh += stueck
        return roh[:grenze], len(roh) > grenze
    except (OSError, ValueError):
        return None
    finally:
        for fd in fds:
            os.close(fd)


def stand_lesen(vid: int) -> dict | None:
    """Nur bekannte Felder mit passendem Typ; None, wenn es keinen brauchbaren Stand gibt."""
    gelesen = datei_lesen(vid, "stand.json", STAND_MAX)
    if not gelesen or gelesen[1]:
        return None
    try:
        roh = json.loads(gelesen[0].decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(roh, dict) or roh.get("status") not in STATUS_STAND:
        return None
    zeit = roh.get("zeit")
    stand = {"status": roh["status"],
             "zeit": float(zeit) if isinstance(zeit, (int, float)) and not isinstance(zeit, bool) else 0.0}
    if roh.get("branch") == f"werkbank/{vid}":
        stand["branch"] = roh["branch"]
    if isinstance(roh.get("tests"), str) and _TESTS_OK.fullmatch(roh["tests"]):
        stand["tests"] = roh["tests"]
    if isinstance(roh.get("fehler"), str):
        stand["fehler"] = roh["fehler"][:300]
    if roh.get("uebernahme") in ("laeuft", *UEBERNAHME_ERGEBNIS):
        stand["uebernahme"] = roh["uebernahme"]
        u_zeit = roh.get("uebernahme_zeit")
        stand["uebernahme_zeit"] = (float(u_zeit) if isinstance(u_zeit, (int, float)) and not isinstance(u_zeit, bool)
                                    else 0.0)
        if isinstance(roh.get("uebernahme_text"), str):
            stand["uebernahme_text"] = roh["uebernahme_text"][:300]
    for feld, hoechstens in (("nachbesserungen", MAX_NACHBESSERUNGEN), ("versuch", MAX_NACHBESSERUNGEN + 1)):
        wert = roh.get(feld)
        if isinstance(wert, int) and not isinstance(wert, bool) and 0 <= wert <= hoechstens:
            stand[feld] = wert
    for feld in ("tests_neu_rot", "tests_gesperrt"):
        if isinstance(roh.get(feld), list):
            stand[feld] = [str(x)[:200] for x in roh[feld] if isinstance(x, str)][:50]
    return stand


def _bericht_md(vid: int) -> str:
    gelesen = datei_lesen(vid, "bericht.md", BERICHT_MAX)
    return gelesen[0].decode("utf-8", "replace") if gelesen else ""


def _nachbesserung_text(v: dict) -> tuple[str, str]:
    """(Zusatz hinter „bereit“/„fertig“, eigener Satz): „ nach 1 Nachbesserung“ bzw. ehrlich bei Rot
    „ Die Tests sind trotz 2 Nachbesserungen rot.“ Beides leer ohne Nachbesserung und ohne Rot."""
    n = int(v.get("nachbesserungen") or 0)
    wort = f"{n} Nachbesserung{'' if n == 1 else 'en'}"
    if v.get("tests") == "rot":
        return "", (f" Die Tests sind trotz {wort} rot." if n else " Die Tests sind rot.")
    return (f" nach {wort}" if n else ""), ""


def _melden(v: dict, nach: str) -> None:
    """Je Wechsel einmal; eine Störung beim Melden ändert den Status nicht zurück."""
    titel = str(v.get("titel") or "")[:60]
    zusatz, satz = _nachbesserung_text(v) if (v.get("backend") or "claude") in CLI_BACKENDS else ("", "")
    # OpenCode liefert nur einen Bericht (kein Branch) — dort gibt es nichts zu übernehmen.
    tun = "übernehmen oder verwerfen" if (v.get("backend") or "claude") in CLI_BACKENDS else "den Bericht lesen oder verwerfen"
    try:
        from kern.anliegen import fach as anliegen_fach
        if nach == "bereit":
            anliegen_fach.standard().anlegen("eike", "werkbank", f"Ergebnis zu Vorhaben {v['id']}",
                                             f"Für „{titel}“ liegt ein Ergebnis bereit{zusatz}.{satz} Eike kann es in "
                                             f"der App unter Extras → hphome ansehen und {tun}.")
        else:
            # `fehler` ist nicht neu startbar (starten.STARTBAR): verwerfen, bei Bedarf neu vorschlagen
            anliegen_fach.standard().anlegen("eike", "werkbank", f"Vorhaben {v['id']} hat nicht geklappt",
                                             f"Die Arbeit an „{titel}“ ist fehlgeschlagen. Eike kann es in der App "
                                             "unter Extras → hphome ansehen, verwerfen und bei Bedarf neu vorschlagen.")
    except Exception:                                  # noqa: BLE001
        log.exception("Werkbank: Anliegen zu Vorhaben %s nicht angelegt", v.get("id"))
    if nach != "bereit":
        return
    try:
        from kern.dienste import benachrichtigungen
        benachrichtigungen.senden("werkbank", "Friday: Ergebnis liegt bereit",
                                  f"„{titel}“ ist fertig{zusatz}.{satz} Ansehen und {tun} unter Extras → hphome.",
                                  url="/#studio/hphome", tag=f"werkbank-{v['id']}")
    except Exception:                                  # noqa: BLE001
        log.exception("Werkbank: Push zu Vorhaben %s nicht gesendet", v.get("id"))


def abschliessen(vid: int, nach: str, fach: speicher.Fach | None = None, **felder) -> bool:
    """`laeuft` → `bereit`/`fehler` und melden — nur, wenn dieser Aufruf den Wechsel gemacht hat."""
    fach = fach or speicher.standard()
    if "bericht" in felder:
        felder["bericht"] = _sauber(felder["bericht"], BERICHT_MAX)
    if not fach.uebergang(vid, "laeuft", nach, **felder):
        return False
    v = fach.eintrag(vid) or {"id": vid}
    log.info("Werkbank: Vorhaben %s ist %s", vid, nach)
    _melden(v, nach)
    return True


def _sekunden_seit(iso: str | None, jetzt: datetime) -> float | None:
    try:
        return (jetzt - datetime.fromisoformat(iso)).total_seconds() if iso else None
    except (TypeError, ValueError):
        return None


def _gestartet_epoch(iso: str | None) -> float | None:
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else None
    except (TypeError, ValueError):
        return None


def _bereit_felder(vid: int, stand: dict) -> dict:
    teile = [_bericht_md(vid).strip() or "Kein Bericht von Claude."]
    if stand.get("tests_neu_rot"):
        teile.append("Neu rote Tests: " + ", ".join(stand["tests_neu_rot"]))
    if stand.get("tests_gesperrt"):
        teile.append("Tests nicht gelaufen, weil geschützte Dateien geändert wurden: " + ", ".join(stand["tests_gesperrt"]))
    felder = {"bericht": "\n\n".join(teile), "tests": stand.get("tests") or "nicht_gelaufen",
              "nachbesserungen": stand.get("nachbesserungen", 0)}
    if stand.get("branch"):
        felder["branch"] = stand["branch"]
    if datei_lesen(vid, "diff.patch", 1) is not None:
        felder["diff_pfad"] = f"werkbank/{vid}/diff.patch"
    return felder


_UEBERNAHME_PUSH = {
    "ok": ("Friday: übernommen", "„{titel}“ ist übernommen; Friday läuft mit dem neuen Stand."),
    "zurueckgerollt": ("Friday: Übernahme zurückgerollt", "„{titel}“ ist nicht live: {text}"),
    "fehler": ("Friday: Übernahme fehlgeschlagen", "„{titel}“: {text}"),
}


def _uebernahme_melden(v: dict, ergebnis: str, text: str) -> None:
    titel = str(v.get("titel") or "")[:60]
    kopf, rumpf = _UEBERNAHME_PUSH[ergebnis]
    try:
        from kern.dienste import benachrichtigungen
        benachrichtigungen.senden("werkbank", kopf, rumpf.format(titel=titel, text=text)[:300],
                                  url="/#studio/hphome", tag=f"werkbank-{v['id']}")
    except Exception:                                  # noqa: BLE001
        log.exception("Werkbank: Push zur Übernahme %s nicht gesendet", v.get("id"))


def uebernahmen_nachsehen(fach: speicher.Fach, jetzt: datetime) -> list[int]:
    """Ergebnisse laufender Übernahmen eintragen; eine ohne Ergebnis nach `FRIST_UEBERNAHME_S` gilt als `fehler`."""
    geaendert = []
    for v in fach.uebernahmen_offen():
        vid = v["id"]
        try:
            stand = stand_lesen(vid) or {}
            beginn = _gestartet_epoch(v.get("uebernahme_seit"))
            ergebnis = stand.get("uebernahme")
            if ergebnis in UEBERNAHME_ERGEBNIS and beginn is not None and stand["uebernahme_zeit"] >= beginn - UEBERNAHME_ALT_S:
                text = _sauber(stand.get("uebernahme_text") or "", 300).strip() or {
                    "ok": "Übernommen.", "zurueckgerollt": "Zurückgerollt.", "fehler": "Fehlgeschlagen."}[ergebnis]
            else:
                seit = _sekunden_seit(v.get("uebernahme_seit"), jetzt)
                if seit is not None and seit <= FRIST_UEBERNAHME_S:
                    continue
                ergebnis = "fehler"
                text = (f"Kein Ergebnis nach {FRIST_UEBERNAHME_S // 60} Minuten. Bitte den Stand von main und Friday "
                        "von Hand prüfen.")
            bericht = None
            if ergebnis != "ok":
                alt = str(v.get("bericht") or "")
                bericht = _sauber(f"Übernahme {'zurückgerollt' if ergebnis == 'zurueckgerollt' else 'fehlgeschlagen'}: "
                                  f"{text}" + (f"\n\n{alt}" if alt else ""), BERICHT_MAX)
            if fach.uebernahme_abschliessen(vid, ergebnis, bericht=bericht):
                log.info("Werkbank: Übernahme von Vorhaben %s: %s", vid, ergebnis)
                _uebernahme_melden(v, ergebnis, text)
                geaendert.append(vid)
        except Exception:                              # noqa: BLE001 — ein Vorhaben darf den Lauf nie anhalten
            log.exception("Werkbank: Übernahme von Vorhaben %s nicht verarbeitet", vid)
    return geaendert


def nachbesserung_laufend(v: dict) -> int | None:
    """Für die Karte: Nummer der Nachbesserung, die der Helfer gerade rechnet (None = erster Lauf oder unbekannt).
    Nur aus einem Stand, der zu diesem Lauf gehört."""
    if v.get("status") != "laeuft" or (v.get("backend") or "claude") not in CLI_BACKENDS:
        return None
    stand = stand_lesen(int(v["id"]))
    beginn = _gestartet_epoch(v.get("gestartet"))
    if not stand or beginn is None or stand["zeit"] < beginn - STAND_ALT_S or stand["status"] != "laeuft":
        return None
    n = stand.get("versuch", 1) - 1
    return n if n >= 1 else None


def einmal(fach: speicher.Fach | None = None) -> list[int]:
    """Ein Durchgang über alle laufenden Vorhaben. Gibt die Nummern zurück, deren Status sich geändert hat."""
    fach = fach or speicher.standard()
    jetzt = config.jetzt()
    geaendert = []
    for v in fach.laufende():
        vid = v["id"]
        try:
            seit = _sekunden_seit(v.get("gestartet"), jetzt)
            stand = stand_lesen(vid) if (v.get("backend") or "claude") in CLI_BACKENDS else None
            beginn = _gestartet_epoch(v.get("gestartet"))
            if stand and beginn is not None and stand["zeit"] < beginn - STAND_ALT_S:
                stand = None                                   # vom vorigen Lauf desselben Vorhabens
            if stand and stand["status"] == "bereit":
                if abschliessen(vid, "bereit", fach, **_bereit_felder(vid, stand)):
                    geaendert.append(vid)
            elif stand and stand["status"] == "fehler":
                grund = stand.get("fehler") or "Der Auftrag ist fehlgeschlagen."
                bericht = _bericht_md(vid).strip()
                if abschliessen(vid, "fehler", fach, bericht=f"Fehler: {grund}" + (f"\n\n{bericht}" if bericht else "")):
                    geaendert.append(vid)
            else:
                frist = FRIST_MIT_STAND_S if stand else FRIST_OHNE_STAND_S
                if seit is None or seit > frist:
                    grund = (f"Kein Ergebnis nach {int(frist // 60)} Minuten — der Auftrag ist hängen geblieben."
                             if seit is not None else "Startzeit fehlt — der Auftrag lässt sich nicht verfolgen.")
                    if abschliessen(vid, "fehler", fach, bericht=f"Fehler: {grund}"):
                        geaendert.append(vid)
        except Exception:                              # noqa: BLE001 — ein Vorhaben darf den Lauf nie anhalten
            log.exception("Werkbank: Stand von Vorhaben %s nicht verarbeitet", vid)
    return geaendert + uebernahmen_nachsehen(fach, jetzt)


async def dauerlauf() -> None:
    while True:
        try:
            await asyncio.to_thread(einmal)
        except Exception:                              # noqa: BLE001
            log.exception("Werkbank: Durchgang fehlgeschlagen")
        try:                                           # Prüf-Worker: fertige Prüfungen selbst melden (Push, Übersicht, Chat)
            from kern.werkbank import pruefen
            await asyncio.to_thread(pruefen.nachsehen)
        except Exception:                              # noqa: BLE001
            log.exception("Prüfungen: Nachsehen fehlgeschlagen")
        await asyncio.sleep(TAKT_S)
