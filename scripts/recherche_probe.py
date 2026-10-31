#!/usr/bin/env python3
"""Probelauf der Recherche mit dem ECHTEN Gemini und dem echten Netz (15.10.2026). Temp-Daten; Push, Karte und Gedanke sind Attrappen.
Aufruf nur über `scripts/recherche-probe-isoliert.sh`. Frage: Eikes LUH-Frage. Zeitlimit der Probe: 15 Minuten."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from unittest import mock

sys.path.insert(0, "/app")
os.environ.setdefault("FRIDAY_DATEN", tempfile.mkdtemp())

from kern.dashboard import friday_karten  # noqa: E402
from kern.dienste import benachrichtigungen  # noqa: E402
from kern.recherche import ergebnis, lauf, speicher  # noqa: E402

FRAGE = ("Wann sind die Vorlesungen im Fach Informatik an der Leibniz Universität Hannover für Juniorstudierende (Juniorstudium)? "
         "Ich brauche Semesterzeiten, wie man als Schüler teilnimmt und welche Informatik-Veranstaltungen offen sind.")


async def main() -> int:
    fehler = []
    r = speicher.anlegen(FRAGE, "Bevorzuge offizielle Seiten der Uni Hannover.")
    speicher.beginnen(r["id"])
    t0 = time.time()
    with mock.patch.object(lauf, "ZEITLIMIT_S", 900), mock.patch.object(lauf, "QUOTA_WARTEN_S", 60):
        erg = await lauf.recherchieren(r["id"])
    dauer = int(time.time() - t0)
    h = speicher.holen(r["id"])
    print(f"\nStatus: {erg['status']} nach {dauer} s — Schritte: {h['schritte']} — Sicherheit: {h['sicherheit']}")
    print("\nNOTIZEN:")
    for n in h["notizen"]:
        print(" -", n["text"][:200], "|", n["quelle"][:80])
    print("\nBERICHT:\n" + h["bericht"])
    print("\nOFFEN:", h["offen"])
    print("\nQUELLEN:")
    for q in h["quellen"]:
        print(" -", q["url"], q.get("titel", ""))
    karte, push = mock.AsyncMock(), mock.Mock()
    with mock.patch.object(friday_karten, "legen", karte), mock.patch.object(benachrichtigungen, "senden", push):
        await ergebnis.abliefern(r["id"])
    print("\nPush:", push.call_args.args[1:3] if push.call_args else None)
    print("Karte:", (karte.await_args.args[0], karte.await_args.args[1][:120]) if karte.await_args else None)
    if erg["status"] != "fertig":
        fehler.append("Lauf nicht fertig")
    if (h["schritte"].get("seiten") or 0) < 2:
        fehler.append("weniger als 2 Seiten gelesen")
    if not any("uni-hannover" in q["url"] for q in h["quellen"]):
        fehler.append("keine Quelle von uni-hannover.de")
    if len(h["bericht"]) < 80:
        fehler.append("Bericht zu kurz")
    print("\n" + ("ALLES OK" if not fehler else "FEHLER: " + "; ".join(fehler)))
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
