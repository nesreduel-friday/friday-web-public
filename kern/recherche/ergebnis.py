"""Ein fertiger Rechercheauftrag wird gemeldet (15.10.2026): Push, Karte in der Übersicht und ein Gedanke im Gedankenbuch.

Der volle Bericht bleibt im Speicher (Friday liest ihn im Chat vor: `recherche_lesen`). Push und Karte tragen nur die Kurzfassung ohne Adressen (auch Adressen im Berichtstext werden entfernt).
Der Gedanke gilt als Fremdtext-Zitat (Webseiten), die Quelle ist „Recherche #n“. Jede Zugabe ist abgesichert: ein Fehler bei Karte oder Gedanke
darf den Push nie kippen."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from kern.dashboard import friday_karten
from kern.denken import buch
from kern.dienste import benachrichtigungen
from kern.recherche import speicher

log = logging.getLogger("friday.recherche")

KARTE_MINUTEN = 720
HINWEIS = "Aus Webseiten, nicht von mir geprüft — Quellen prüfen."


_ADRESSE = re.compile(r"\(?\s*(?:Quelle:?\s*)?https?://\S+\s*\)?[.,;]?", re.I)


def _kurz(bericht: str, laenge: int) -> str:
    """Höchstens `laenge` Zeichen (mit „ …“ am Ende, wenn gekürzt)."""
    t = " ".join(bericht.split())
    if len(t) <= laenge:
        return t
    return t[:laenge - 2].rsplit(" ", 1)[0].rstrip(",;:- ")[:laenge - 2] + " …"


async def abliefern(rid: int) -> None:
    r = speicher.holen(rid)
    if not r or r["status"] in ("abgebrochen", "wartend", "laeuft"):
        return
    frage = " ".join(r["frage"].split())
    if r["status"] == "fehler":
        try:
            benachrichtigungen.senden("friday", "Recherche gescheitert", f"„{_kurz(frage, 80)}“ hat nicht geklappt: {_kurz(r['fehler'] or 'unbekannter Fehler', 60)}", url="/#chat")
        except Exception:  # noqa: BLE001
            log.exception("Recherche %s: Fehler-Push nicht gesendet", rid)
        return
    try:
        benachrichtigungen.senden("friday", "Recherche fertig", _kurz(f"Zu „{_kurz(frage, 70)}“ gibt es ein Ergebnis — frag mich danach.", 160), url="/#chat")
    except Exception:  # noqa: BLE001
        log.exception("Recherche %s: Push nicht gesendet", rid)
    try:
        text = f"{_kurz(_ADRESSE.sub('', r['bericht']), 400 - len(HINWEIS) - 1)} {HINWEIS}"
        await friday_karten.legen(f"Recherche: {_kurz(frage, 60)}", text[:400], minuten=KARTE_MINUTEN)
    except Exception:  # noqa: BLE001
        log.exception("Recherche %s: Karte nicht gelegt", rid)
    try:
        hosts = list(dict.fromkeys((urlparse(q["url"]).hostname or "")[:60] for q in r["quellen"] if q.get("url")))[:4]
        b = buch.standard()
        runde = b.runde_beginnen()
        b.runde_beenden(runde, "Recherche", "recherche", "", "")        # zählt nicht als Denkrunde (`runden_heute`, Takt)
        e = b.gedanke_festhalten(runde, "erkenntnis", _kurz(_ADRESSE.sub("", f"{frage}: {r['bericht']}"), 400),
                                 [f"Recherche #{rid}"] + [h for h in hosts if h], r["sicherheit"] if r["sicherheit"] == "belegt" else "vermutet", fremd=True)
        if e.get("ok"):
            speicher.gedanke_setzen(rid, e["id"])
        else:
            log.warning("Recherche %s: Gedanke abgelehnt (%s)", rid, e.get("grund"))
    except Exception:  # noqa: BLE001
        log.exception("Recherche %s: Gedanke nicht festgehalten", rid)
