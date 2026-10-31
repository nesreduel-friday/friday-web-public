"""Claude-Code-Sitzungen mit Fernzugriff — starten, sehen, beenden (Extras → Claude).

Friday steckt im Container und startet selbst keine Prozesse auf dem Host. Dieses Modul
legt nur einen Auftrag als Datei in `data/claude/auftrag.json` ab; ausgeführt wird er vom
Helfer auf hphome (`host-helfer/claude_helfer.py`), der seinerseits `data/claude/zustand.json`
schreibt. Läuft der Helfer nicht, veraltet der Zustand — dann sagt die App das, statt zu warten.

**Kein Werkzeug fürs Modell.** Wie beim Abschicken der Garantieanträge führt hier nur ein
Tipp von Eike in der App etwas aus; Gemini hat keinen Weg hierher. Gestartete Sitzungen
laufen mit `--permission-mode manual`, fragen also vor jeder Änderung nach.

**Werkbank (27.10.2026):** `werkbank_auftrag` legt den Prompt nach `data/werkbank/<vid>/prompt.md`
und den Auftrag `werkbank` mit nur der Vorhaben-Id ab; der Helfer startet Claude Code im
Worktree und schreibt `data/werkbank/<vid>/stand.json`. Auch das ist kein Werkzeug fürs Modell
(der Weg dorthin läuft über `kern/werkbank/`, mit Eikes Ja im Code geprüft).

**Übernehmen (28.10.2026):** `uebernehmen_auftrag` legt den Auftrag `uebernehmen` (nur die Vorhaben-Id) ab;
der Helfer merged `werkbank/<vid>` ins Live-Repo und baut Friday neu.
Aufgerufen NUR von `kern/werkbank/uebernehmen.py`, und das nur von der Route (Eikes Knopf) — nie
von einem Werkzeug (Test `test_werkbank_uebernehmen.KeinWegFuerDasModell`).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

from kern import config

NAME_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
VORHABEN_OK = re.compile(r"[0-9]{1,6}")      # wie im Helfer, immer mit fullmatch
FRISCH_S = 15          # älter heißt: der Helfer läuft nicht mehr
WARTEN_S = 10.0        # so lange warten wir auf die Antwort des Helfers
# Werkbank-Start: länger als die Frische-Grenze des Helfers (`WERKBANK_MAX_ALTER_S` = 15 s) plus Takt. Kommt bis dahin
# keine Antwort, kann der liegengebliebene Auftrag nicht mehr starten (sonst liefe er unbemerkt am Tageslimit vorbei).
WERKBANK_WARTEN_S = 20.0
TAKT_S = 0.25


class ClaudeFehler(Exception):
    """Der Text ist für Eike gedacht und geht unverändert in die App."""


class HelferStumm(ClaudeFehler):
    """Der Auftrag liegt, aber der Helfer hat nicht geantwortet — er kann ihn später noch ausführen."""


def _ordner() -> Path:
    return config.daten_ordner() / "claude"


def name_saeubern(name: str) -> str:
    """Aus einer Eingabe einen erlaubten Sitzungsnamen machen — leer heißt: selbst benennen."""
    sauber = re.sub(r"[^a-z0-9-]+", "-", (name or "").strip().lower())
    sauber = re.sub(r"-{2,}", "-", sauber).strip("-")[:24].strip("-")
    if not sauber:
        sauber = "sitzung-" + config.jetzt().strftime("%H%M")
    if not NAME_OK.match(sauber):
        raise ClaudeFehler("Der Name darf nur Kleinbuchstaben, Ziffern und Bindestriche enthalten.")
    return sauber


def zustand() -> dict:
    """Was der Helfer zuletzt gemeldet hat. Fehlt oder veraltet er, steht das drin."""
    datei = _ordner() / "zustand.json"
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"helfer": False, "sitzungen": [], "max": 0,
                "text": "Der Helfer auf hphome läuft nicht (host-helfer/claude_helfer.py)."}
    alter = time.time() - float(daten.get("aktualisiert") or 0)
    if alter > FRISCH_S:
        return {"helfer": False, "sitzungen": daten.get("sitzungen") or [], "max": daten.get("max") or 0,
                "text": f"Der Helfer meldet sich seit {int(alter)} s nicht mehr."}
    return {"helfer": True, "sitzungen": daten.get("sitzungen") or [], "max": daten.get("max") or 0,
            "werkbank": daten.get("werkbank") or {"laeuft": None}}


def _antwort_abwarten(auftrag_id: str, warten_s: float = WARTEN_S) -> dict:
    ende = time.time() + warten_s
    while time.time() < ende:
        time.sleep(TAKT_S)
        try:
            daten = json.loads((_ordner() / "zustand.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        antwort = daten.get("letzte_antwort") or {}
        if antwort.get("id") == auftrag_id:
            return {"ok": bool(antwort.get("ok")), "text": str(antwort.get("text") or ""),
                    "sitzungen": daten.get("sitzungen") or []}
    raise HelferStumm("Der Helfer auf hphome antwortet nicht.")


def _auftrag(aktion: str, name: str = "", vorhaben: str = "", warten_s: float = WARTEN_S, weiter: bool = False,
             nachfrage: bool = False, backend: str = "") -> dict:
    if aktion not in ("starten", "beenden", "werkbank", "uebernehmen", "pruefen"):
        raise ClaudeFehler("Unbekannte Aktion.")
    if aktion in ("werkbank", "uebernehmen", "pruefen") and not VORHABEN_OK.fullmatch(vorhaben or ""):
        raise ClaudeFehler("Ungültige Vorhaben-Id.")
    if not zustand().get("helfer"):
        raise ClaudeFehler("Der Helfer auf hphome läuft nicht — ohne ihn geht das nicht.")
    ordner = _ordner()
    ordner.mkdir(parents=True, exist_ok=True)
    auftrag_id = secrets.token_hex(8)
    datei, tmp = ordner / "auftrag.json", ordner / "auftrag.tmp"
    # `zeit`: der Helfer verwirft ein liegengebliebenes `uebernehmen` (älter als 60 s) bzw. `werkbank` (älter als 15 s)
    if aktion in ("werkbank", "uebernehmen", "pruefen"):
        inhalt = {"id": auftrag_id, "aktion": aktion, "vorhaben": vorhaben, "zeit": int(time.time())}
        if weiter and aktion == "werkbank":
            inhalt["weiter"] = True
        if aktion == "werkbank":
            inhalt["backend"] = backend or "claude"
        if aktion == "pruefen":
            inhalt["backend"] = backend or "deepseek"
            if nachfrage:
                inhalt["nachfrage"] = True
    else:
        inhalt = {"id": auftrag_id, "aktion": aktion, "name": name, "zeit": int(time.time())}
    tmp.write_text(json.dumps(inhalt, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(datei)
    return _antwort_abwarten(auftrag_id, warten_s)


def starten(name: str = "") -> dict:
    return _auftrag("starten", name_saeubern(name))


def beenden(name: str) -> dict:
    if not NAME_OK.match((name or "").strip()):
        raise ClaudeFehler("Unbekannter Name.")
    return _auftrag("beenden", name.strip())


def werkbank_auftrag(vorhaben_id: int, prompt: str, weiter: bool = False, backend: str = "claude") -> dict:
    """Claude Code im Worktree für ein Vorhaben starten. Wirft `ClaudeFehler` mit Klartext."""
    if isinstance(vorhaben_id, bool) or not isinstance(vorhaben_id, int) or not VORHABEN_OK.fullmatch(str(vorhaben_id)):
        raise ClaudeFehler("Ungültige Vorhaben-Id.")
    text = (prompt or "").strip()
    if not text:
        raise ClaudeFehler("Der Auftrag für Claude ist leer.")
    if not zustand().get("helfer"):
        raise ClaudeFehler("Der Helfer auf hphome läuft nicht — ohne ihn geht das nicht.")
    vid = str(vorhaben_id)
    ablage = config.daten_ordner() / "werkbank" / vid
    ablage.mkdir(parents=True, exist_ok=True)
    datei, tmp = ablage / "prompt.md", ablage / "prompt.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(datei)
    antwort = _auftrag("werkbank", vorhaben=vid, warten_s=WERKBANK_WARTEN_S, weiter=weiter, backend=backend)
    if not antwort.get("ok"):
        raise ClaudeFehler(antwort.get("text") or "Der Helfer hat den Werkbank-Auftrag abgelehnt.")
    return antwort


def pruef_auftrag(pruef_id: int, frage: str, nachfrage: bool = False, backend: str = "deepseek") -> dict:
    """Eine Machbarkeitsfrage an den Prüf-Worker (nur lesend) legen. Wirft `ClaudeFehler` mit Klartext, `HelferStumm` wenn der
    Helfer nicht antwortet. Die Frage liegt als `data/werkbank/pruef-<id>/frage.md`, die Antwort kommt als `antwort.md`."""
    if isinstance(pruef_id, bool) or not isinstance(pruef_id, int) or not VORHABEN_OK.fullmatch(str(pruef_id)):
        raise ClaudeFehler("Ungültige Prüfungs-Id.")
    text = (frage or "").strip()
    if not text:
        raise ClaudeFehler("Die Frage für den Prüf-Worker ist leer.")
    if not zustand().get("helfer"):
        raise ClaudeFehler("Der Helfer auf hphome läuft nicht — ohne ihn geht das nicht.")
    ablage = config.daten_ordner() / "werkbank" / f"pruef-{pruef_id}"
    ablage.mkdir(parents=True, exist_ok=True)
    datei, tmp = ablage / "frage.md", ablage / "frage.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(datei)
    antwort = _auftrag("pruefen", vorhaben=str(pruef_id), warten_s=WERKBANK_WARTEN_S, nachfrage=nachfrage, backend=backend)
    if not antwort.get("ok"):
        raise ClaudeFehler(antwort.get("text") or "Der Helfer hat die Prüfung abgelehnt.")
    return antwort


def uebernehmen_auftrag(vorhaben_id: int) -> dict:
    """Das Ergebnis eines Vorhabens live bringen (Helfer: Merge, Neubau, Gesundheit, Rückfall). Wirft `ClaudeFehler`
    mit Klartext, wenn der Helfer fehlt oder ablehnt (schmutziger Baum, fehlender Branch, geschützte Pfade …).
    Geschützte Pfade sind hart gesperrt — einen Schalter dafür gibt es nicht."""
    if isinstance(vorhaben_id, bool) or not isinstance(vorhaben_id, int) or not VORHABEN_OK.fullmatch(str(vorhaben_id)):
        raise ClaudeFehler("Ungültige Vorhaben-Id.")
    antwort = _auftrag("uebernehmen", vorhaben=str(vorhaben_id))
    if not antwort.get("ok"):
        raise ClaudeFehler(antwort.get("text") or "Der Helfer hat das Übernehmen abgelehnt.")
    return antwort
