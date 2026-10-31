"""Der Arbeiter im Container `snapmem`: nimmt Aufträge als Dateien auf, rechnet, meldet Stand.

Ordner (SNAPMEM_ORDNER, Standard /snapmem):
  auftraege/<id>.json         von Friday geschrieben: {"id", "zips": [Dateinamen in uploads/], "modus"}
  uploads/<name>.zip          hochgeladene Export-ZIPs
  ergebnisse/<id>.zip         Ergebnis
  ergebnisse/<id>.status.json vom Arbeiter geschrieben: Zustand, Fortschritt, Zahlen, Meldung
Zustände: wartet, laeuft, fertig, fehler. Ein beim Start noch „laeuft“ gefundener Auftrag war
unterbrochen und wird als Fehler gemeldet (nie stillschweigend neu gerechnet).
"""

import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

from .pipeline import MODI, run

ORDNER = Path(os.environ.get("SNAPMEM_ORDNER", "/snapmem"))
PLATZ_FAKTOR = 2.5       # freier Platz, den ein Lauf mindestens braucht (Vielfaches der ZIP-Größe)
TAKT_S = 2.0


def _schreiben(pfad: Path, daten: dict) -> None:
    tmp = pfad.with_suffix(pfad.suffix + ".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, pfad)


def status_lesen(ordner: Path, auftrag_id: str) -> dict | None:
    pfad = ordner / "ergebnisse" / f"{auftrag_id}.status.json"
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _status(ordner: Path, auftrag_id: str, **felder) -> dict:
    alt = status_lesen(ordner, auftrag_id) or {}
    alt.update(felder)
    _schreiben(ordner / "ergebnisse" / f"{auftrag_id}.status.json", alt)
    return alt


def naechster(ordner: Path) -> Path | None:
    """Ältester Auftrag ohne Status oder mit Status „wartet“."""
    kandidaten = sorted((ordner / "auftraege").glob("*.json"), key=lambda p: p.stat().st_mtime)
    for pfad in kandidaten:
        st = status_lesen(ordner, pfad.stem)
        if st is None or st.get("zustand") == "wartet":
            return pfad
    return None


def aufraeumen_nach_absturz(ordner: Path) -> None:
    for st_pfad in (ordner / "ergebnisse").glob("*.status.json"):
        st = status_lesen(ordner, st_pfad.name.removesuffix(".status.json"))
        if st and st.get("zustand") == "laeuft":
            _status(ordner, st_pfad.name.removesuffix(".status.json"), zustand="fehler",
                    meldung="Der Lauf wurde unterbrochen (Arbeiter neu gestartet). Bitte neu starten.")
            (ordner / "ergebnisse" / (st_pfad.name.removesuffix(".status.json") + ".zip")).unlink(missing_ok=True)


def bearbeiten(ordner: Path, auftrag_pfad: Path) -> None:
    auftrag_id = auftrag_pfad.stem
    try:
        auftrag = json.loads(auftrag_pfad.read_text(encoding="utf-8"))
        modus = auftrag["modus"]
        if modus not in MODI:
            raise ValueError(f"Unbekannter Modus: {modus}")
        namen = auftrag["zips"]
        if not namen or any("/" in n or "\\" in n or n.startswith(".") for n in namen):
            raise ValueError("Ungültige ZIP-Namen im Auftrag.")
        pfade = [ordner / "uploads" / n for n in namen]
        fehlend = [p.name for p in pfade if not p.is_file()]
        if fehlend:
            raise ValueError(f"ZIP nicht gefunden: {', '.join(fehlend)}")
        (ordner / "ergebnisse").mkdir(exist_ok=True)
        groesse = sum(p.stat().st_size for p in pfade)
        frei = shutil.disk_usage(ordner).free
        if frei < groesse * PLATZ_FAKTOR:
            raise ValueError(f"Zu wenig Platz: frei {frei // 2**20} MB, nötig etwa {int(groesse * PLATZ_FAKTOR) // 2**20} MB.")
    except Exception as exc:
        (ordner / "ergebnisse").mkdir(exist_ok=True)
        _status(ordner, auftrag_id, zustand="fehler", meldung=str(exc))
        return

    start = time.time()
    _status(ordner, auftrag_id, zustand="laeuft", modus=modus, fertig=0, gesamt=0, schritt="Start", gestartet=start)
    zeit = [0.0]

    def fortschritt(fertig: int, gesamt: int, schritt: str) -> None:
        if time.time() - zeit[0] >= 1.0 or fertig >= gesamt:
            zeit[0] = time.time()
            _status(ordner, auftrag_id, fertig=fertig, gesamt=gesamt, schritt=schritt)

    ziel = ordner / "ergebnisse" / f"{auftrag_id}.zip"
    try:
        zaehler = run(pfade, modus, ziel, fortschritt)
    except Exception as exc:
        print(traceback.format_exc(), file=sys.stderr)
        ziel.unlink(missing_ok=True)
        _status(ordner, auftrag_id, zustand="fehler", meldung=f"{type(exc).__name__}: {exc}")
        return
    _status(ordner, auftrag_id, zustand="fertig", zaehler=dict(zaehler), groesse=ziel.stat().st_size,
            dauer=round(time.time() - start, 1), meldung="")


def main() -> None:
    for unter in ("auftraege", "uploads", "ergebnisse"):
        (ORDNER / unter).mkdir(parents=True, exist_ok=True)
    aufraeumen_nach_absturz(ORDNER)
    print("snapmem-Arbeiter bereit", flush=True)
    while True:
        pfad = naechster(ORDNER)
        if pfad is None:
            time.sleep(TAKT_S)
            continue
        print(f"Auftrag {pfad.stem}", flush=True)
        bearbeiten(ORDNER, pfad)


if __name__ == "__main__":
    main()
