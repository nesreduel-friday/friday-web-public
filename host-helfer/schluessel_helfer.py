"""Rund um Gemini-Schlüssel, was Friday selbst nicht kann — der Helfer auf hphome.

Zwei Aufträge: **`anlegen`** — ein neues Google-Cloud-Projekt samt Schlüssel, ein
Schlüssel pro Projekt, sonst teilen sich mehrere ein Tageslimit (siehe
docs/superpowers/specs/2026-09-22-gemini-schluessel-pool-design.md). **`graphiti_umschalten`**
— den Graphiti-Container auf einen anderen Schlüssel umstellen, wenn seiner am Tageslimit
ist (Friday kann ihm keinen fremden Schlüssel während des Laufens unterschieben, nur
`.env` ändern und neu starten).

Friday selbst hält kein Google-Login und keinen Docker-Zugriff und legt deshalb nur
einen Auftrag in `data/schluessel_auftraege/auftrag.json` ab; dieser Helfer führt ihn
aus und schreibt das Ergebnis nach `data/schluessel_auftraege/zustand.json`.
Voraussetzung fürs Anlegen: `gcloud` ist installiert und einmalig mit dem Google-Konto
angemeldet (`gcloud auth login`), das die neuen Projekte bekommen soll.

Start von Hand (zum Prüfen):  python3 host-helfer/schluessel_helfer.py
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from pathlib import Path

TAKT_S = 5.0
API = "generativelanguage.googleapis.com"
AKTIVIEREN_VERSUCHE = 6
AKTIVIEREN_ABSTAND_S = 5.0
ZEITLIMIT_S = 120.0


def ordner() -> Path:
    daten = Path(os.getenv("FRIDAY_DATEN", "") or (Path(__file__).resolve().parent.parent / "data"))
    p = daten / "schluessel_auftraege"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _gcloud(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gcloud", *args, "--quiet"], capture_output=True, text=True, timeout=ZEITLIMIT_S)


def _projekt_id() -> str:
    return "friday-pool-" + secrets.token_hex(4)


def _fehlertext(p: subprocess.CompletedProcess) -> str:
    text = (p.stderr or p.stdout or "unbekannter Fehler").strip()
    return (text.splitlines()[-1] if text else "unbekannter Fehler")[:200]


def _keystring_lesen(projekt_id: str, key_name: str) -> str:
    p = _gcloud("services", "api-keys", "get-key-string", key_name, "--project", projekt_id, "--format=json")
    if p.returncode != 0:
        raise RuntimeError(_fehlertext(p))
    return json.loads(p.stdout)["keyString"]


def anlegen() -> dict:
    """Neues Projekt, API aktivieren, einen auf diese API beschränkten Schlüssel erzeugen.

    Die JSON-Feldnamen unten (`response.name`, `keyString`) sind der Stand von `gcloud`
    im Januar 2026 — vor dem ersten echten Einsatz einmal gegen echtes `gcloud` prüfen
    (Projekt-Regel „erst messen, dann ändern“) und hier anpassen, falls sich das geändert hat."""
    projekt_id = _projekt_id()
    p = _gcloud("projects", "create", projekt_id, "--name", projekt_id, "--format=json")
    if p.returncode != 0:
        return {"ok": False, "text": f"Projekt anlegen fehlgeschlagen: {_fehlertext(p)}"}

    letzter_fehler = ""
    for _ in range(AKTIVIEREN_VERSUCHE):
        p = _gcloud("services", "enable", API, "--project", projekt_id)
        if p.returncode == 0:
            break
        letzter_fehler = _fehlertext(p)
        time.sleep(AKTIVIEREN_ABSTAND_S)
    else:
        return {"ok": False, "text": f"API aktivieren fehlgeschlagen: {letzter_fehler}"}

    p = _gcloud("services", "api-keys", "create", "--project", projekt_id,
                "--display-name", projekt_id, "--api-target", f"service={API}", "--format=json")
    if p.returncode != 0:
        return {"ok": False, "text": f"Schlüssel anlegen fehlgeschlagen: {_fehlertext(p)}"}
    try:
        key_name = json.loads(p.stdout)["response"]["name"]
        wert = _keystring_lesen(projekt_id, key_name)
    except (ValueError, KeyError, RuntimeError) as exc:
        return {"ok": False, "text": f"Antwort von gcloud nicht lesbar: {exc}"}

    return {"ok": True, "text": "Schlüssel angelegt.", "wert": wert, "projekt_id": projekt_id}


def _env_datei() -> Path:
    return Path(__file__).resolve().parent.parent / ".env"


def _env_setzen(zeilen: list[str], werte: dict[str, str]) -> list[str]:
    rest = dict(werte)
    neu = []
    for zeile in zeilen:
        name = zeile.split("=", 1)[0]
        if name in rest:
            neu.append(f"{name}={rest.pop(name)}")
        else:
            neu.append(zeile)
    for name, wert in werte.items():
        if name in rest:
            neu.append(f"{name}={wert}")
    return neu


def graphiti_umschalten(schluessel: str) -> dict:
    """Den Graphiti-Container auf einen anderen Gemini-Schlüssel umschalten und neu starten
    (`docker compose up -d graphiti` liest `.env` neu und ersetzt den Container, wenn sich ein Wert geändert hat):
    `GRAPH_GEMINI_API_KEY` setzen."""
    pfad = _env_datei()
    try:
        zeilen = pfad.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"ok": False, "text": f".env nicht lesbar: {exc}"}
    neu = _env_setzen(zeilen, {"GRAPH_GEMINI_API_KEY": schluessel})
    try:
        pfad.write_text("\n".join(neu) + "\n", encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "text": f".env nicht schreibbar: {exc}"}
    p = subprocess.run(["docker", "compose", "up", "-d", "graphiti"],
                        cwd=pfad.parent, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        return {"ok": False, "text": f"Container-Neustart fehlgeschlagen: {_fehlertext(p)}"}
    return {"ok": True, "text": "Wissensgraph umgeschaltet."}


def _schreiben(pfad: Path, daten: dict) -> None:
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(pfad)


def _auftrag_ausfuehren(auftrag: dict) -> dict:
    aktion = str(auftrag.get("aktion") or "")
    if aktion == "anlegen":
        ergebnis = anlegen()
    elif aktion == "graphiti_umschalten":
        ergebnis = graphiti_umschalten(str(auftrag.get("schluessel") or ""))
    else:
        ergebnis = {"ok": False, "text": "Unbekannte Aktion."}
    return {"id": str(auftrag.get("id") or "")[:64], "zeit": int(time.time()), **ergebnis}


def lauf(takt: float = TAKT_S, runden: int | None = None) -> None:
    o = ordner()
    auftrag_datei, zustand_datei = o / "auftrag.json", o / "zustand.json"
    try:
        letzte_antwort: dict = json.loads(zustand_datei.read_text(encoding="utf-8")).get("letzte_antwort") or {}
    except (OSError, ValueError):
        letzte_antwort = {}
    runde = 0
    while runden is None or runde < runden:
        runde += 1
        try:
            if auftrag_datei.exists():
                try:
                    auftrag = json.loads(auftrag_datei.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    auftrag = {}
                auftrag_datei.unlink(missing_ok=True)
                if auftrag:
                    letzte_antwort = _auftrag_ausfuehren(auftrag)
            _schreiben(zustand_datei, {"aktualisiert": int(time.time()), "letzte_antwort": letzte_antwort})
        except Exception as exc:                                  # der Helfer darf nie sterben
            print(f"[schluessel-helfer] Fehler: {exc}", flush=True)
        if runden is None or runde < runden:
            time.sleep(takt)


if __name__ == "__main__":
    print(f"[schluessel-helfer] läuft, Aufträge aus {ordner()}", flush=True)
    lauf()
