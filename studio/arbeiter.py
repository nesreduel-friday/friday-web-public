"""Arbeiter für die Videoproduktion — wartet auf Aufträge, rendert in Ruhe.

Warum ein Dauerdienst mit Auftragsordner und nicht „Friday startet je Video
einen Container": Dafür bräuchte Friday den Docker-Socket, und wer den hat, ist
faktisch Administrator des ganzen Servers. Hier legt Friday nur eine Datei ab.

Ablauf:
  data/studio/auftraege/<kennung>.json   Friday legt ihn ab
  -> läuft (Status im selben Auftrag)
  -> data/studio/fertig/<kennung>.json   + das fertige MP4 daneben

Ein Auftrag darf so lange dauern, wie er braucht (Eikes Wunsch). Es läuft immer
nur EINER: Der Renderer schreibt seine Zwischendateien in feste Pfade, zwei
Läufe gleichzeitig würden sich gegenseitig überschreiben.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ORDNER = Path(os.getenv("STUDIO_ORDNER", "/studio"))
AUFTRAEGE = ORDNER / "auftraege"
FERTIG = ORDNER / "fertig"
RENDERER = Path("/renderer")
AUSGABE = RENDERER / "assets" / "final" / "final_short.mp4"

# Kein Endlos-Lauf: Bleibt ein Render hängen, soll der Auftrag scheitern statt
# den Arbeiter für immer zu blockieren. Großzügig, weil Gemini und Pexels
# zwischendurch zäh sein können.
ZEITLIMIT_S = int(os.getenv("STUDIO_ZEITLIMIT", "3600"))
TAKT_S = 10


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _melden(text: str) -> None:
    print(f"[Studio] {text}", flush=True)


def _schreiben(ziel: Path, daten: dict) -> None:
    """Atomar schreiben — Friday liest denselben Ordner, halbe Dateien wären
    schlimmer als gar keine."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    # 600 statt der Vorgabe: Solange ein Auftrag läuft, stehen die Schlüssel
    # zwangsläufig darin — dann sollen sie wenigstens niemandem sonst offenliegen.
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=2)
    os.replace(neu, ziel)


def _aufraeumen() -> None:
    """Rohmaterial nach jedem Lauf wegräumen. Auf Eikes Server sind nur noch
    28 GB frei — Stockvideos und Zwischenschnitte je Lauf sind schnell ein
    Vielfaches des fertigen Videos."""
    for unterordner in ("temp", "audio_clips", "video_clips"):
        pfad = RENDERER / "assets" / unterordner
        if not pfad.is_dir():
            continue
        for eintrag in pfad.iterdir():
            try:
                shutil.rmtree(eintrag) if eintrag.is_dir() else eintrag.unlink()
            except OSError as exc:
                _melden(f"konnte {eintrag.name} nicht wegräumen: {exc}")


# Der Renderer meldet seine Phasen mit Emoji-Markern. Daraus wird ein echter
# Fortschritt statt nur „die letzte Zeile" — Eike soll auf der Studioseite sehen,
# WO ein Lauf steht, nicht bloß, dass er noch läuft.
PHASEN = [
    ("🧹", "räumt auf", 5),
    ("🚀", "startet", 10),
    ("🎯", "wählt das Thema", 15),
    ("📝", "schreibt das Skript", 30),
    ("🔍", "sucht Bildmaterial", 50),
    ("⚙", "schneidet die Szenen", 70),
    ("📣", "brennt die Untertitel ein", 80),
    ("🎬", "fügt alles zusammen", 90),
    ("✅", "fertig", 100),
]


def _phase(zeile: str, bisher: int) -> tuple[str, int] | None:
    """Welche Phase meldet diese Zeile? Rückschritte werden ignoriert — der
    Renderer sucht pro Szene erneut Material, das darf den Balken nicht
    zurückwerfen."""
    for marker, name, anteil in PHASEN:
        if marker in zeile and anteil >= bisher:
            return name, anteil
    return None


def _rendern(auftrag: dict, melden=None) -> tuple[bool, str, Path | None]:
    """Einen Auftrag abarbeiten. Gibt (Erfolg, Meldung, Videodatei) zurück.

    `melden(schritt, prozent, zeile)` wird während des Laufs aufgerufen, damit
    der Fortschritt in der Auftragsdatei landet.
    """
    thema = str(auftrag.get("thema") or "").strip()
    if not thema:
        return False, "Der Auftrag nennt kein Thema.", None

    umgebung = dict(os.environ)
    umgebung["TOPIC"] = thema
    if not auftrag.get("untertitel", True):
        umgebung["NO_CAPTIONS"] = "1"
    # Die Schlüssel kommen aus Fridays Zugangsspeicher über die Umgebung,
    # nicht aus einer .env im Abbild — dort hätten Geheimnisse nichts zu suchen.
    for name in ("GEMINI_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "GEMINI_MODEL"):
        wert = auftrag.get("schluessel", {}).get(name) or os.getenv(name, "")
        if wert:
            umgebung[name] = wert

    if AUSGABE.exists():
        AUSGABE.unlink()          # nie ein altes Video als neues ausgeben

    _melden(f"rendere: {thema}")
    # Zeilenweise mitlesen statt stumm sammeln: Sonst sieht man minutenlang
    # nichts und kann einen hängenden Lauf nicht von einem arbeitenden
    # unterscheiden.
    zeilen: list[str] = []
    prozent, schritt = 0, "startet"
    beginn = time.monotonic()
    try:
        lauf = subprocess.Popen(
            [sys.executable, "main.py"], cwd=str(RENDERER), env=umgebung,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1)
    except OSError as exc:
        return False, f"Der Renderer ließ sich nicht starten: {exc}", None

    try:
        for roh in lauf.stdout or []:
            zeile = roh.rstrip()
            if zeile:
                zeilen.append(zeile)
            if time.monotonic() - beginn > ZEITLIMIT_S:
                lauf.kill()
                return False, f"Abbruch: länger als {ZEITLIMIT_S // 60} Minuten gelaufen.", None
            neu = _phase(zeile, prozent)
            if neu:
                schritt, prozent = neu
            # Die Szenen sind der längste Abschnitt — Feinanzeige dafür.
            if "Processing Scene" in zeile:
                schritt = zeile.split("Processing Scene", 1)[1].strip()[:40]
                schritt = f"schneidet Szene {schritt}"
            if melden:
                melden(schritt, prozent, zeile[:160])
        lauf.wait(timeout=60)
    except subprocess.TimeoutExpired:
        lauf.kill()
        return False, "Abbruch: Der Renderer reagierte am Ende nicht mehr.", None

    letzte = "\n".join(zeilen[-20:])
    if lauf.returncode != 0:
        return False, f"Der Renderer brach ab (Code {lauf.returncode}).\n{letzte}", None
    if not AUSGABE.exists():
        return False, f"Der Lauf endete ohne Video.\n{letzte}", None
    return True, "fertig", AUSGABE


def _abarbeiten(datei: Path) -> None:
    try:
        auftrag = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _melden(f"Auftrag {datei.name} unlesbar: {exc}")
        datei.rename(datei.with_suffix(".kaputt"))
        return

    kennung = datei.stem
    auftrag["begonnen"] = _jetzt()
    auftrag["status"] = "laeuft"
    auftrag["schritt"] = "startet"
    auftrag["fortschritt"] = 0
    _schreiben(datei, auftrag)

    # Nicht bei jeder Zeile schreiben: Der Renderer redet viel, und die Datei
    # liegt auf der Platte, die Friday mitliest.
    letzte_schreibung = [0.0]

    def fortschritt(schritt: str, prozent: int, zeile: str) -> None:
        auftrag.update(schritt=schritt, fortschritt=prozent, meldung=zeile)
        jetzt = time.monotonic()
        if jetzt - letzte_schreibung[0] >= 2.0:
            letzte_schreibung[0] = jetzt
            _schreiben(datei, auftrag)

    erfolg, meldung, video = _rendern(auftrag, fortschritt)

    if erfolg and video:
        name = f"{kennung}.mp4"
        ziel = FERTIG / name
        ziel.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video, ziel)
        auftrag.update(status="fertig", video=name,
                       groesse_mb=round(ziel.stat().st_size / 1024 / 1024, 1))
        _melden(f"fertig: {name} ({auftrag['groesse_mb']} MB)")
    else:
        auftrag.update(status="fehler", fehler=meldung)
        _melden(f"gescheitert: {meldung.splitlines()[0] if meldung else '?'}")

    auftrag["beendet"] = _jetzt()
    # Die Schlüssel haben im Ergebnis nichts verloren: Der Auftrag brauchte sie,
    # die Ablage nicht. Sonst lägen Gemini-, Pexels- und Pixabay-Schlüssel
    # dauerhaft in fertig/ (gefunden 14.09.2026, nach dem ersten echten Lauf).
    auftrag.pop("schluessel", None)
    _schreiben(FERTIG / f"{kennung}.json", auftrag)
    datei.unlink(missing_ok=True)
    _aufraeumen()


def main() -> None:
    AUFTRAEGE.mkdir(parents=True, exist_ok=True)
    FERTIG.mkdir(parents=True, exist_ok=True)
    _melden(f"bereit, wartet auf Aufträge in {AUFTRAEGE}")
    while True:
        offen = sorted(p for p in AUFTRAEGE.glob("*.json") if not p.name.endswith(".neu.json"))
        if offen:
            _abarbeiten(offen[0])      # bewusst einer nach dem anderen
        else:
            time.sleep(TAKT_S)


if __name__ == "__main__":
    main()
