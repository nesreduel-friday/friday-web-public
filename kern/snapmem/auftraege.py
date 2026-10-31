"""Snapchat-Memories (Friday-Seite): Upload in Teilen, Aufträge, Stand, Ergebnis, Aufräumen.

Friday rechnet nichts. Die ZIPs liegen in `data/snapmem/uploads/`, ein Auftrag ist eine Datei in
`data/snapmem/auftraege/`; der Container `snapmem` (snapmem/snapmem/arbeiter.py) rechnet und schreibt
`ergebnisse/<id>.status.json` und `ergebnisse/<id>.zip`. Alles bleibt auf dem Server.

Upload in Teilen, weil ein Export mehrere GB groß sein kann und die globale Grenze für Uploads
darunter liegt: erst `upload_start`, dann jedes Teil (feste Größe `TEIL`), dann `upload_fertig`.
"""

import json
import os
import re
import secrets
import shutil
import time
import zipfile
from pathlib import Path

from kern import config

TEIL = 32 * 1024 * 1024
ZIP_MAX = 25 * 1024 ** 3
PLATZ_FAKTOR = 3          # Upload + Ergebnis + Zwischenstand
MODI = ("alles", "sauber", "personen")
AUFBEWAHREN_TAGE = 7
_ID = re.compile(r"^[0-9a-f]{12}$")


class SnapmemFehler(Exception):
    """Fehler mit einem Text, den Eike lesen darf."""


def ordner() -> Path:
    basis = config.daten_ordner() / "snapmem"
    for unter in ("auftraege", "uploads", "ergebnisse"):
        (basis / unter).mkdir(parents=True, exist_ok=True)
    return basis


def _id(wert: str) -> str:
    if not isinstance(wert, str) or not _ID.match(wert):
        raise SnapmemFehler("Ungültige Kennung.")
    return wert


def _schreiben(pfad: Path, daten: dict) -> None:
    tmp = pfad.with_suffix(pfad.suffix + ".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, pfad)


def _lesen(pfad: Path) -> dict | None:
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sauber(name: str) -> str:
    name = Path(str(name)).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[-80:]
    return name or "export.zip"


# ---------- Upload ----------

def upload_start(name: str, groesse: int) -> dict:
    if not str(name).lower().endswith(".zip"):
        raise SnapmemFehler("Bitte die ZIP aus dem Snapchat-Export hochladen (.zip).")
    if not isinstance(groesse, int) or groesse <= 0:
        raise SnapmemFehler("Die Größe der Datei fehlt.")
    if groesse > ZIP_MAX:
        raise SnapmemFehler(f"Die Datei ist größer als {ZIP_MAX // 1024 ** 3} GB.")
    basis = ordner()
    frei = shutil.disk_usage(basis).free
    if frei < groesse * PLATZ_FAKTOR:
        raise SnapmemFehler(f"Zu wenig Platz auf dem Server: frei {frei // 2 ** 20} MB, "
                            f"nötig etwa {groesse * PLATZ_FAKTOR // 2 ** 20} MB.")
    uid = secrets.token_hex(6)
    teil = basis / "uploads" / f"{uid}.teil"
    with open(teil, "wb") as f:
        f.truncate(groesse)
    _schreiben(basis / "uploads" / f"{uid}.meta", {"id": uid, "name": _sauber(name), "groesse": groesse,
                                                   "teile": (groesse + TEIL - 1) // TEIL, "da": []})
    return {"id": uid, "teil": TEIL, "teile": (groesse + TEIL - 1) // TEIL}


def upload_teil(uid: str, nr: int, daten: bytes) -> dict:
    uid = _id(uid)
    basis = ordner()
    meta = _lesen(basis / "uploads" / f"{uid}.meta")
    if meta is None:
        raise SnapmemFehler("Unbekannter Upload.")
    if not isinstance(nr, int) or not 0 <= nr < meta["teile"]:
        raise SnapmemFehler("Ungültige Teilnummer.")
    erwartet = min(TEIL, meta["groesse"] - nr * TEIL)
    if len(daten) != erwartet:
        raise SnapmemFehler(f"Teil {nr} hat {len(daten)} statt {erwartet} Bytes.")
    with open(basis / "uploads" / f"{uid}.teil", "r+b") as f:
        f.seek(nr * TEIL)
        f.write(daten)
    if nr not in meta["da"]:
        meta["da"].append(nr)
        _schreiben(basis / "uploads" / f"{uid}.meta", meta)
    return {"da": len(meta["da"]), "von": meta["teile"]}


def upload_fertig(uid: str) -> dict:
    uid = _id(uid)
    basis = ordner() / "uploads"
    meta = _lesen(basis / f"{uid}.meta")
    if meta is None:
        raise SnapmemFehler("Unbekannter Upload.")
    fehlend = meta["teile"] - len(set(meta["da"]))
    if fehlend:
        raise SnapmemFehler(f"Es fehlen noch {fehlend} Teile.")
    teil = basis / f"{uid}.teil"
    if not zipfile.is_zipfile(teil):
        teil.unlink(missing_ok=True)
        (basis / f"{uid}.meta").unlink(missing_ok=True)
        raise SnapmemFehler("Das ist keine gültige ZIP-Datei.")
    ziel = basis / f"{uid}-{meta['name']}"
    os.replace(teil, ziel)
    (basis / f"{uid}.meta").unlink(missing_ok=True)
    return {"id": uid, "datei": ziel.name, "name": meta["name"], "groesse": ziel.stat().st_size}


def uploads() -> list[dict]:
    """Fertig hochgeladene ZIPs, neueste zuerst."""
    liste = []
    for p in (ordner() / "uploads").iterdir():
        m = re.match(r"^([0-9a-f]{12})-(.+\.zip)$", p.name)
        if m and p.is_file():
            st = p.stat()
            liste.append({"id": m.group(1), "datei": p.name, "name": m.group(2), "groesse": st.st_size,
                          "datum": st.st_mtime})
    return sorted(liste, key=lambda x: x["datum"], reverse=True)


def upload_loeschen(uid: str) -> None:
    uid = _id(uid)
    for p in (ordner() / "uploads").glob(f"{uid}*"):
        p.unlink(missing_ok=True)


# ---------- Aufträge ----------

def auftrag_anlegen(zips: list[str], modus: str) -> str:
    if modus not in MODI:
        raise SnapmemFehler("Unbekannter Modus.")
    if not zips or not isinstance(zips, list):
        raise SnapmemFehler("Keine ZIP ausgewählt.")
    vorhanden = {u["datei"] for u in uploads()}
    for z in zips:
        if z not in vorhanden:
            raise SnapmemFehler("Eine der ZIPs gibt es nicht (mehr).")
    aid = secrets.token_hex(6)
    _schreiben(ordner() / "auftraege" / f"{aid}.json",
               {"id": aid, "zips": list(zips), "modus": modus, "erstellt": time.time()})
    return aid


def status(aid: str) -> dict:
    aid = _id(aid)
    basis = ordner()
    auftrag = _lesen(basis / "auftraege" / f"{aid}.json")
    if auftrag is None:
        raise SnapmemFehler("Diesen Auftrag gibt es nicht.")
    st = _lesen(basis / "ergebnisse" / f"{aid}.status.json") or {"zustand": "wartet"}
    return {**auftrag, **st, "ergebnis": ergebnis_pfad(aid) is not None}


def ergebnis_pfad(aid: str) -> Path | None:
    aid = _id(aid)
    p = ordner() / "ergebnisse" / f"{aid}.zip"
    st = _lesen(ordner() / "ergebnisse" / f"{aid}.status.json") or {}
    return p if p.is_file() and st.get("zustand") == "fertig" else None


def laeufe(limit: int = 20) -> list[dict]:
    pfade = sorted((ordner() / "auftraege").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [status(p.stem) for p in pfade[:limit] if _ID.match(p.stem)]


def loeschen(aid: str) -> None:
    """Entfernt Auftrag, Stand und Ergebnis. Die hochgeladenen ZIPs bleiben."""
    aid = _id(aid)
    basis = ordner()
    st = _lesen(basis / "ergebnisse" / f"{aid}.status.json") or {}
    if st.get("zustand") == "laeuft":
        raise SnapmemFehler("Der Lauf ist noch aktiv.")
    (basis / "auftraege" / f"{aid}.json").unlink(missing_ok=True)
    for p in (basis / "ergebnisse").glob(f"{aid}.*"):
        p.unlink(missing_ok=True)


def aufraeumen(tage: int = AUFBEWAHREN_TAGE) -> None:
    """Löscht alte Ergebnisse, Aufträge und Uploads (und liegengebliebene Teil-Uploads)."""
    basis = ordner()
    grenze = time.time() - tage * 86400
    aktiv = set()
    for p in (basis / "auftraege").glob("*.json"):
        if p.stat().st_mtime < grenze:
            continue        # ein Auftrag, der selbst gleich gelöscht wird, hält keinen Upload fest
        st = _lesen(basis / "ergebnisse" / f"{p.stem}.status.json") or {"zustand": "wartet"}
        if st.get("zustand") in ("wartet", "laeuft"):
            aktiv.update((_lesen(p) or {}).get("zips", []))
    for unter in ("ergebnisse", "auftraege"):
        for p in (basis / unter).iterdir():
            if p.is_file() and p.stat().st_mtime < grenze:
                p.unlink(missing_ok=True)
    for p in (basis / "uploads").iterdir():
        if p.is_file() and p.stat().st_mtime < grenze and p.name not in aktiv:
            p.unlink(missing_ok=True)
