"""Übernehmen per Knopf (Spec 2026-10-27-werkbank, Task 8): ein bereitliegendes Ergebnis live bringen.

NUR von der Route `POST /api/werkbank/<id>/uebernehmen` aufgerufen (Eikes Tipp in der App) — kein Werkzeug, keine Denkrunde,
kein anderes Modul (Test `test_werkbank_uebernehmen.KeinWegFuerDasModell` wacht darüber, dass kein Katalog-Modul hierher kommt).

Ablauf: Prüfen (Status `bereit`, eigener Branch, Tests `ok` oder ausdrücklich `trotz_rot`, Helfer da) → Status atomar auf
`uebernommen` + `uebernahme = laeuft` (VOR dem Auftrag: ein Doppelklick wendet nicht zweimal an, der zweite bekommt 409) →
Auftrag an den Helfer. Lehnt der Helfer ab, geht der Status zurück auf `bereit`, der Klartext an Eike **und in den Bericht
des Vorhabens** (30.10.2026): Die App zeigt die Antwort nur als eine Zeile am Ende der Liste; nach dem Neuladen sah die
Karte sonst wieder aus wie vorher, als wäre nichts passiert. Bei `bereit` zeigt die Karte den Bericht.
Das Ergebnis des Helfers (Merge, Neubau, Gesundheit, Rückfall) trägt `lauf.uebernahmen_nachsehen` ein — Friday startet dabei ja neu.
"""

from __future__ import annotations

import logging

from kern.dienste import claude_sitzung
from kern.werkbank import speicher

log = logging.getLogger("friday.werkbank")

NICHTS = "NICHTS übernommen."
ABLEHNUNG = "Übernahme nicht möglich:"


def _nein(text: str, code: int = 409) -> dict:
    return {"ok": False, "text": f"{NICHTS} {text}", "code": code}


def _ablehnung(bericht, text: str) -> str:
    """Bericht mit dem Grund der Ablehnung obenauf. Ein früherer Ablehnungskopf wird ersetzt (kein Stapeln bei
    wiederholten Tipps), der Bericht des Laufs darunter bleibt erhalten."""
    grund = str(text).strip()[:300]
    alt = str(bericht or "")
    if alt.startswith(ABLEHNUNG):
        _, _, alt = alt.partition("\n\n")
    return f"{ABLEHNUNG} {grund}" + (f"\n\n{alt}" if alt else "")


def _abgelehnt(fach: speicher.Fach, v: dict, text: str, code: int = 409) -> dict:
    """Ablehnung, die Eike auch nach einem Neuladen noch sieht: Der Grund steht am Vorhaben (Karte bei `bereit`)."""
    fach.felder_setzen(int(v["id"]), bericht=_ablehnung(v.get("bericht"), text))
    return _nein(text, code)


def uebernehmen_dict(vid: int, trotz_rot: bool = False) -> dict:
    """{"ok", "text"} (bei Fehlschlag auch "code" für die Route)."""
    fach = speicher.standard()
    v = fach.eintrag(int(vid))
    if not v:
        return _nein("Dieses Vorhaben gibt es nicht.", 404)
    if v["status"] == "uebernommen":
        return _nein("Das wird schon übernommen oder ist übernommen.")
    if v["status"] != "bereit":
        return _nein("Übernehmen geht nur, wenn ein Ergebnis bereitliegt.")
    if (v.get("backend") or "claude") not in ("claude", "deepseek") or v.get("branch") != f"werkbank/{v['id']}":
        return _abgelehnt(fach, v, "Zu diesem Ergebnis gibt es keinen Branch, den man übernehmen könnte.")
    if v.get("tests") != "ok" and trotz_rot is not True:
        return _abgelehnt(fach, v, f"Die Tests sind nicht grün ({v.get('tests') or 'nicht gelaufen'}). Übernehmen nur "
                                   "mit ausdrücklichem „trotz rot“.")
    z = claude_sitzung.zustand()
    if not z.get("helfer"):
        return _abgelehnt(fach, v, z.get("text") or "Der Helfer auf hphome läuft nicht — ohne ihn geht das nicht.")
    if not fach.uebergang(v["id"], "bereit", "uebernommen", uebernahme="laeuft",
                          uebernahme_seit=speicher._iso(speicher._jetzt())):
        return _nein("Das wird schon übernommen oder ist übernommen.")
    try:
        claude_sitzung.uebernehmen_auftrag(int(v["id"]))
    except claude_sitzung.HelferStumm:
        # Der Auftrag liegt; der Helfer kann ihn noch ausführen. Status bleibt (sonst wäre ein zweiter Klick möglich),
        # `lauf.uebernahmen_nachsehen` trägt das Ergebnis ein oder meldet nach der Frist „bitte prüfen“.
        return {"ok": False, "code": 504, "text": "Der Helfer hat nicht geantwortet. Ob er übernimmt, melde ich, sobald "
                                                  "es feststeht."}
    except Exception as e:                             # noqa: BLE001 — sonst bliebe das Vorhaben ohne Auftrag gesperrt
        text = str(e) if isinstance(e, claude_sitzung.ClaudeFehler) else f"Der Auftrag ließ sich nicht ablegen: {e}"
        # Der Grund gehört mit an das Vorhaben: Sonst steht er nur in dieser Antwort, und die Karte sieht nach dem
        # Neuladen wieder aus wie vorher (30.10.2026).
        fach.uebergang(v["id"], "uebernommen", "bereit", uebernahme=None, uebernahme_seit=None,
                       bericht=_ablehnung(v.get("bericht"), text))
        return _nein(text)
    log.info("Werkbank: Übernahme von Vorhaben %s beauftragt (trotz_rot=%s)", v["id"], trotz_rot is True)
    return {"ok": True, "text": "Ich übernehme das Ergebnis. Friday startet dabei neu; danach melde ich, ob es geklappt hat."}
