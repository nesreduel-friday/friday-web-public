"""Selbstentwicklung (29.10.2026): Friday übernimmt fertig gebaute Werkzeug-Verbesserungen selbst, wenn Eike den Hebel
`werkbank_auto_uebernehmen` in der App umgelegt hat (Eikes Entscheidung: „automatisch bei grünen Tests“).

Der Hebel schaltet zwei Dinge: Die Automatik startet dann auch Vorhaben aus der Denkrunde (sie liest Eikes Gespräche und leitet daraus
fehlende Werkzeuge ab; nie fremder Text), und `einmal` übernimmt ein bereitliegendes Ergebnis ohne Knopf — aber nur, wenn ALLES gilt:
- Tests `ok` (nie „trotz rot“), Backend claude/deepseek, nicht fremd, höchstens `TAGESLIMIT` automatische Übernahmen am Tag (Knopf `werkbank_selbst_limit`, Standard an), keine
  andere Übernahme in Arbeit.
- Der Diff ist vollständig lesbar und bleibt in der Positivliste `diff_ok`: Werkzeug-Module (ohne die Sicherheitsdateien),
  Tests, Doku. Bestehende Tests dürfen nur wachsen (keine entfernten oder geänderten Zeilen), `katalog.py` nur Modulnamen in
  Import und `_MODULE` ergänzen. Alles andere bleibt beim Knopf.
Der Merge selbst, der Neubau, die Gesundheitsprobe und der Rückfall sind der bestehende Weg des Helfers (`uebernehmen_dict`)."""

from __future__ import annotations

import logging
import re

from kern.werkbank import lauf, speicher, uebernehmen

log = logging.getLogger("friday.werkbank")

AN = ("aus", "an")
TAGESLIMIT = 3
DIFF_MAX = 400_000
DATEIEN_MAX = 8
ZEILEN_MAX = 800
# Sicherheitskern der Werkzeuge: nie unbeaufsichtigt ändern
GESPERRT = {"katalog.py", "rechte.py", "sendepruefung.py", "personenprimitive.py", "module_schalten.py", "__init__.py",
            "skills.py", "auswahl.py"}
_DATEI = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
# katalog.py: jede geänderte Zeile darf nur Modulnamen, Kommas und die Klammern/Kopfzeilen von Import und `_MODULE` tragen
_KATALOG_ZEILE = re.compile(r"^[+-](from kern\.werkzeuge import \(|_MODULE = \[)?[A-Za-z0-9_, ()\]]*$")
# Eine Zeile, die einen bestehenden Test entschärft statt ihn zu ergänzen (30.10.2026): `@unittest.skip`,
# `self.skipTest(...)`, `@unittest.expectedFailure`, `if False:`. Eine „+"-Zeile ist sonst erlaubt (Tests dürfen wachsen).
_TEST_ENTSCHAERFT = re.compile(r"unittest\.skip|skipTest|expectedFailure|unittest\.SkipTest|\bif\s+False\b")


def eingeschaltet() -> bool:
    from kern.planer import briefing
    return briefing._lesen(briefing._einstellungsdatei()).get("werkbank_auto_uebernehmen") == "an"


def limit_an() -> bool:
    """Das Tageslimit ist Standard an; Eike kann es in der App abschalten (dann keine Obergrenze, alle anderen Grenzen bleiben)."""
    from kern.planer import briefing
    return briefing._lesen(briefing._einstellungsdatei()).get("werkbank_selbst_limit") != "aus"


def limit_einstellen(wahl: str) -> str:
    """Nur für die Route (Eikes Knopf in der App)."""
    if wahl not in AN:
        raise ValueError(f"Unbekannter Wert: {wahl}")
    from kern.planer import briefing
    with briefing._sperre:
        d = briefing._lesen(briefing._einstellungsdatei())
        d["werkbank_selbst_limit"] = wahl
        briefing._schreiben(briefing._einstellungsdatei(), d)
    return "an" if limit_an() else "aus"


def einstellen(wahl: str) -> str:
    """Nur für die Route (Eikes Hebel in der App)."""
    if wahl not in AN:
        raise ValueError(f"Unbekannter Wert: {wahl}")
    from kern.planer import briefing
    with briefing._sperre:
        d = briefing._lesen(briefing._einstellungsdatei())
        d["werkbank_auto_uebernehmen"] = wahl
        briefing._schreiben(briefing._einstellungsdatei(), d)
    return "an" if eingeschaltet() else "aus"


def diff_ok(diff: str) -> tuple[bool, str]:
    """(erlaubt, Grund bei Nein). Prüft Pfade und Art der Änderung eines `git diff`."""
    dateien: list[tuple[str, list[str], bool]] = []          # (Pfad, geänderte Zeilen, neu angelegt)
    for zeile in diff.splitlines():
        if m := _DATEI.match(zeile):
            if m.group(1) != m.group(2):
                return False, f"Umbenennung: {m.group(1)}"
            dateien.append((m.group(2), [], False))
        elif zeile.startswith("new file mode") and dateien:
            dateien[-1] = (dateien[-1][0], dateien[-1][1], True)
        elif zeile.startswith(("deleted file mode", "rename ", "Binary files", "GIT binary patch", "old mode", "new mode")):
            return False, f"Löschung, Binärdatei oder Rechteänderung: {dateien[-1][0] if dateien else '?'}"
        elif dateien and zeile[:1] in "+-" and not zeile.startswith(("+++", "---")):
            dateien[-1][1].append(zeile)
    if not dateien:
        return False, "leerer Diff"
    if len(dateien) > DATEIEN_MAX or sum(len(z) for _, z, _ in dateien) > ZEILEN_MAX:
        return False, "zu groß für die Automatik"
    for pfad, zeilen, neu in dateien:
        teile = pfad.split("/")
        if ".." in teile:
            return False, f"Pfad: {pfad}"
        if teile[0] == "docs":
            continue
        if teile[0] == "test" and len(teile) == 2 and teile[1].startswith("test_") and teile[1].endswith(".py"):
            if not neu and any(z.startswith("-") for z in zeilen):
                return False, f"bestehender Test würde geändert: {pfad}"
            if not neu and any(z.startswith("+") and _TEST_ENTSCHAERFT.search(z) for z in zeilen):
                return False, f"Test würde entschärft: {pfad}"
            continue
        if teile[:2] == ["kern", "werkzeuge"] and len(teile) == 3 and teile[2].endswith(".py"):
            if teile[2] == "katalog.py":
                if neu or not all(_KATALOG_ZEILE.match(z) for z in zeilen):
                    return False, "katalog.py: nur Modulnamen in Import und _MODULE"
                continue
            if teile[2] in GESPERRT:
                return False, f"Sicherheitsdatei: {pfad}"
            continue
        return False, f"außerhalb der Positivliste: {pfad}"
    return True, ""


def _tagesbeginn_zaehler(fach: speicher.Fach) -> int:
    return fach._alle("SELECT COUNT(*) AS n FROM vorhaben WHERE auto_uebernommen >= ?", (speicher._tagesbeginn(),))[0]["n"]


def _melden(v: dict, text: str) -> None:
    try:
        from kern.dienste import benachrichtigungen
        benachrichtigungen.senden("werkbank", "Friday: baut sich selbst um", text, url="/#studio/hphome",
                                  tag=f"werkbank-selbst-{v['id']}", chat_text=text)
    except Exception:                                          # noqa: BLE001
        log.exception("Werkbank: Meldung zur Selbstübernahme von %s nicht gesendet", v.get("id"))


def einmal(fach: speicher.Fach | None = None) -> dict | None:
    """Ein Takt: höchstens EIN Ergebnis übernehmen. None = nichts getan."""
    if not eingeschaltet():
        return None
    fach = fach or speicher.standard()
    if fach.uebernahmen_offen() or (limit_an() and _tagesbeginn_zaehler(fach) >= TAGESLIMIT):
        return None
    for v in fach._alle("SELECT * FROM vorhaben WHERE status = 'bereit' ORDER BY id"):
        if v["fremd"] or v.get("tests") != "ok" or (v.get("backend") or "claude") not in ("claude", "deepseek") \
                or v.get("uebernahme") in ("ok", "zurueckgerollt", "fehler") or v.get("auto_uebernommen"):
            continue
        roh = lauf.datei_lesen(int(v["id"]), "diff.patch", DIFF_MAX)
        if roh is None or roh[1]:
            continue
        ok, grund = diff_ok(roh[0].decode("utf-8", "replace"))
        if not ok:
            log.info("Werkbank: Selbstübernahme von %s nicht erlaubt (%s) — bleibt beim Knopf", v["id"], grund)
            continue
        d = uebernehmen.uebernehmen_dict(int(v["id"]))
        if d["ok"]:
            # Erst nach dem Erfolg festhalten (30.10.2026): Vorher blockierte ein transienter Fehlschlag
            # (Helfer weg, 504) dieses Vorhaben dauerhaft für die Automatik und zählte ins Tageslimit.
            fach.felder_setzen(int(v["id"]), auto_uebernommen=speicher._iso(speicher._jetzt()))
            log.info("Werkbank: Vorhaben %s wird von selbst übernommen", v["id"])
            _melden(v, f"Ich übernehme „{str(v.get('titel') or '')[:60]}“ von selbst (Tests grün). Ich starte dabei neu "
                       "und melde, ob es geklappt hat; bei Problemen rolle ich zurück.")
        else:
            log.info("Werkbank: Selbstübernahme von %s abgelehnt: %s", v["id"], d["text"][:200])
        return d
    return None
