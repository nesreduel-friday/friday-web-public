#!/usr/bin/env python3
"""Latenz der Snapchat-Antworten aus data/snapchat/ergebnisse.jsonl (nur lesen, 07.10.2026). Aufruf auf dem Host:
python3 host-helfer/snap_latenz.py [Datei]. `auswerten`: Zeit vom Eingang bis zum gesendeten Auftrag (Zeilen mit `ausloeser`);
`sende_zeiten`: Zeit vom Legen des Auftrags bis zum Ergebnis (alle gesendeten Zeilen mit `gelegt`)."""
import json
import statistics
import sys
from pathlib import Path


def _p90(werte: list[float]) -> float:
    werte = sorted(werte)
    return werte[max(0, int(len(werte) * 0.9) - 1)]


def sende_zeiten(zeilen: list[dict]) -> dict:
    ok = [z for z in zeilen if z.get("ergebnis") == "gesendet" and z.get("gelegt") and z.get("zeit")]
    if not ok:
        return {"anzahl": 0}
    d = [z["zeit"] - z["gelegt"] for z in ok]
    return {"anzahl": len(ok), "median_s": statistics.median(d), "p90_s": _p90(d)}


def auswerten(zeilen: list[dict]) -> dict:
    ok = [z for z in zeilen if z.get("ergebnis") == "gesendet" and z.get("ausloeser") and z.get("gelegt")]
    if not ok:
        return {"anzahl": 0}
    gesamt = [z["zeit"] - z["ausloeser"] for z in ok]
    senden = [z["zeit"] - z["gelegt"] for z in ok]
    return {"anzahl": len(ok), "median_s": statistics.median(gesamt), "p90_s": _p90(gesamt),
            "sende_median_s": statistics.median(senden), "sende_p90_s": _p90(senden)}


if __name__ == "__main__":
    pfad = Path(sys.argv[1] if len(sys.argv) > 1 else "data/snapchat/ergebnisse.jsonl")
    zeilen = [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines() if z.strip()]
    print(json.dumps({"vom_eingang": auswerten(zeilen), "sende_weg": sende_zeiten(zeilen)}, indent=2, ensure_ascii=False))
