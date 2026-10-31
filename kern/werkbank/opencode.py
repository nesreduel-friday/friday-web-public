"""Client für die OpenCode-Sandbox (Dienst `werkbank-sandbox`).

Datenaustausch über Dateien: `data/werkbank/sandbox/<id>/in.json` ablegen, `out.json` abwarten. Der Ordner
ist für den Sandbox-Container beschreibbar, also ist alles darin nicht vertrauenswürdig: Das Ergebnis wird
nur als reines JSON gelesen (nie ausgeführt), in der Größe begrenzt, Symlinks und Nicht-Dateien werden
abgelehnt.
"""
import asyncio
import json
import os
import secrets
import stat
import time

from kern import config

MAX_ANTWORT = 1_000_000
TAKT_S = 0.5


class OpenCodeFehler(Exception):
    """Klartext auf Deutsch, taugt für Meldungen an Eike."""


def _wurzel():
    return config.daten_ordner() / "werkbank" / "sandbox"


def _ablegen(ordner, prompt: str, schema: dict, modell: str) -> None:
    ordner.mkdir(parents=True, exist_ok=True)
    # Alles über einen Ordner-fd ohne Symlink-Folgen: Hat der Container dort einen Symlink untergeschoben, wirft
    # O_NOFOLLOW (OSError) — nie bekommt ein fremdes Ziel 0777 oder unsere in.json.
    dfd = os.open(ordner, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(dfd, 0o777)          # der Container läuft mit eigener uid und schreibt out.json hinein
        fd = os.open("in.tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"prompt": prompt, "schema": schema, "modell": modell}, f, ensure_ascii=False)
            os.fchmod(f.fileno(), 0o644)   # lesbar für den Container (uid kann abweichen), geschrieben nur vom Host
        os.replace("in.tmp", "in.json", src_dir_fd=dfd, dst_dir_fd=dfd)
    finally:
        os.close(dfd)


def _ergebnis_lesen(ordner) -> dict | None:
    """None, solange es kein out.json gibt. Wirft OpenCodeFehler bei allem Unbrauchbaren."""
    pfad = ordner / "out.json"
    try:
        st = os.lstat(pfad)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise OpenCodeFehler("Die Antwort der Sandbox ist keine normale Datei.")
    if st.st_size > MAX_ANTWORT:
        raise OpenCodeFehler("Die Antwort der Sandbox ist zu groß.")
    try:
        fd = os.open(pfad, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise OpenCodeFehler("Die Antwort der Sandbox lässt sich nicht öffnen.")
    with os.fdopen(fd, "rb") as f:
        roh = f.read(MAX_ANTWORT + 1)
    if len(roh) > MAX_ANTWORT:
        raise OpenCodeFehler("Die Antwort der Sandbox ist zu groß.")
    try:
        d = json.loads(roh.decode("utf-8"))
    except ValueError:
        raise OpenCodeFehler("Die Antwort der Sandbox ist kein gültiges JSON.")
    if not isinstance(d, dict):
        raise OpenCodeFehler("Die Antwort der Sandbox hat ein unerwartetes Format.")
    return d


async def json_anfrage(prompt: str, schema: dict, modell: str = "", frist_s: int = 600) -> dict:
    """Frage an ein freies OpenCode-Modell, Antwort als geprüftes JSON-Objekt (Schema prüft der Läufer)."""
    ordner = _wurzel() / secrets.token_hex(8)
    try:
        await asyncio.to_thread(_ablegen, ordner, prompt, schema, modell)
    except OSError as e:
        raise OpenCodeFehler(f"Der Auftrag für die Sandbox ließ sich nicht ablegen: {e.strerror or e}")
    ende = time.monotonic() + frist_s
    while True:
        d = await asyncio.to_thread(_ergebnis_lesen, ordner)
        if d is not None:
            break
        if time.monotonic() >= ende:
            raise OpenCodeFehler(f"Die Sandbox hat nicht innerhalb von {frist_s} Sekunden geantwortet. "
                                 "Läuft der Dienst (docker compose --profile werkbank up -d werkbank-sandbox)?")
        await asyncio.sleep(TAKT_S)
    if not d.get("ok"):
        raise OpenCodeFehler("Die Sandbox meldet: " + str(d.get("fehler") or "unbekannter Fehler")[:300])
    ergebnis = d.get("json")
    if not isinstance(ergebnis, dict):
        raise OpenCodeFehler("Die Sandbox lieferte kein JSON-Objekt.")
    return ergebnis
