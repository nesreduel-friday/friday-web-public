"""Hinweise der Denkrunde an Eike — selten und begrenzt.

`normal` → Karte auf der Übersicht, `hoch` → Push über `benachrichtigungen.senden` (legt die Meldung selbst auf der
Übersicht ab, deshalb dann keine zweite Karte). Die Grenzen prüft der Server, nicht das Modell.
"""

from __future__ import annotations

import logging

from kern.denken.buch import Buch, _sauber, gleicher_sinn, zu_lang

log = logging.getLogger("friday.denken")

MAX_PRO_RUNDE = 1
MAX_PRO_TAG = 3
DUBLETTE_STUNDEN = 48
KARTE_MINUTEN = 720
TITEL_MAX = 80
TEXT_MAX = 300


async def geben(b: Buch, runde: int, titel: str, text: str, wichtigkeit: str = "normal", fremd: bool = False) -> dict:
    if absage := zu_lang(titel, TITEL_MAX) or zu_lang(text, TEXT_MAX):
        return {"ok": False, "grund": absage}
    titel, text = _sauber(titel, TITEL_MAX), _sauber(text, TEXT_MAX)
    wichtigkeit = "hoch" if wichtigkeit == "hoch" else "normal"
    if not titel:
        return {"ok": False, "grund": "Der Hinweis braucht einen Titel."}
    if not b.gedaechtnis_geprueft(runde):
        return {"ok": False, "grund": "Prüfe zuerst im Gedächtnis (wissen, rueckblick oder erinnern), ob Eike das so "
                                      "gewollt hat — vieles, was nach Fehler aussieht, hat er selbst so bestellt."}
    if b.hinweise_in_runde(runde) >= MAX_PRO_RUNDE:
        return {"ok": False, "grund": "In dieser Runde gab es schon einen Hinweis."}
    if b.hinweise_heute() >= MAX_PRO_TAG:
        return {"ok": False, "grund": f"Heute gab es schon {MAX_PRO_TAG} Hinweise an Eike."}
    for alt in b.hinweise_seit(DUBLETTE_STUNDEN):
        # Titel ODER Text: dieselbe Kollision wird in der nächsten Runde gern anders betitelt und anders erklärt.
        if gleicher_sinn(alt["titel"], titel) or gleicher_sinn(alt["text"], text):
            return {"ok": False, "grund": "Das habe ich Eike schon gemeldet."}
    if wichtigkeit == "hoch" and not b.luecke_belegt(runde):
        wichtigkeit = "normal"                         # „hoch“ nur mit belegter Lücke (≥ 2 Quellen) in dieser Runde
    try:
        if wichtigkeit == "hoch":
            from kern.dienste import benachrichtigungen
            benachrichtigungen.senden("nachdenken", titel, text, url="/#gedaechtnis/gedanken")
            weg = "push"
        else:
            from kern.dashboard import friday_karten
            await friday_karten.legen(f"Gedanke: {titel}", text, minuten=KARTE_MINUTEN)
            weg = "karte"
    except Exception as exc:                           # noqa: BLE001
        log.warning("Hinweis nicht zugestellt: %s", type(exc).__name__)
        return {"ok": False, "grund": "Der Hinweis ließ sich nicht zustellen."}
    b.hinweis_ablegen(runde, titel, text, wichtigkeit)
    try:
        from kern.anliegen import fach
        fach.standard().anlegen("eike", "hinweis", titel, text, dringlichkeit=wichtigkeit, fremd=fremd)
    except Exception:                                  # noqa: BLE001
        log.exception("Anliegen zum Hinweis nicht angelegt")
    return {"ok": True, "weg": weg}
