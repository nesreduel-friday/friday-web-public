#!/usr/bin/env python3
"""Docker-Zugang für den Werkbank-Worker (Eikes Entscheidung 28.10.2026): einzige Tür zu `docker`.

Der Worker darf `docker` nie selbst aufrufen (Berechtigungsliste des Helfers), nur dieses Skript:

    werkbank_docker.py ps
    werkbank_docker.py logs <container> [zeilen]
    werkbank_docker.py inspect <container>
    werkbank_docker.py start|stop|restart <container>
    werkbank_docker.py exec <container> <befehl> [argumente …]      (ohne Shell, ohne Terminal)

Erlaubt sind alle Container AUSSER Immich (Name `immich*` oder Compose-Projekt `immich`) und Fridays eigenem Stack
(Compose-Projekt `friday-web`: dort liegen `data/` und `.env`, die der Worker nie lesen darf). Die Prüfung steht hier, nicht im
Prompt. Läuft im vertrauten Repo (nicht im Worktree), der Worker kann die Datei nicht ändern (`host-helfer/**` ist gesperrt).
Jeder Aufruf steht in `data/werkbank/docker-aufrufe.log` (Zeit, Aktion, Container, kein Ausgabetext).
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "data" / "werkbank" / "docker-aufrufe.log"
NAME_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}")
GESPERRT_PROJEKTE = {"immich", "friday-web"}
AKTIONEN = {"logs", "inspect", "start", "stop", "restart", "exec"}
AUSGABE_MAX = 20_000
ZEITLIMIT_S = 120


def _docker(*args: str, zeit: int = ZEITLIMIT_S) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=zeit, check=False)


def _eintraege() -> list[tuple[str, str]]:
    """[(Name, Compose-Projekt)] aller Container."""
    p = _docker("ps", "-a", "--format", '{{.Names}}\t{{.Label "com.docker.compose.project"}}')
    heraus = []
    for zeile in (p.stdout or "").splitlines():
        name, _, projekt = zeile.partition("\t")
        if name:
            heraus.append((name, projekt.strip()))
    return heraus


def _gesperrt(name: str, projekt: str) -> bool:
    return "immich" in name.lower() or projekt.lower() in GESPERRT_PROJEKTE


def _vermerken(aktion: str, name: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')}\t{aktion}\t{name}\n")
    except OSError:
        pass


def _ausgeben(p: subprocess.CompletedProcess) -> int:
    text = (p.stdout or "") + (p.stderr or "")
    if len(text) > AUSGABE_MAX:
        text = text[:AUSGABE_MAX] + "\n[… gekürzt]"
    sys.stdout.write(text)
    return p.returncode


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    aktion, rest = argv[0], argv[1:]
    if aktion == "ps":
        _vermerken("ps", "-")
        zeilen = ["NAME\tPROJEKT\tSTATUS\tIMAGE"]
        p = _docker("ps", "-a", "--format",
                    '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Status}}\t{{.Image}}')
        for z in (p.stdout or "").splitlines():
            teile = z.split("\t")
            if teile and not _gesperrt(teile[0], teile[1] if len(teile) > 1 else ""):
                zeilen.append(z)
        print("\n".join(zeilen))
        return 0
    if aktion not in AKTIONEN or not rest:
        print(f"Unbekannte Aktion oder Container fehlt. Erlaubt: ps, {', '.join(sorted(AKTIONEN))}.", file=sys.stderr)
        return 2
    name = rest[0]
    if not NAME_OK.fullmatch(name):
        print("Ungültiger Containername.", file=sys.stderr)
        return 2
    bekannt = dict(_eintraege())
    if name not in bekannt:                                      # exakter Name, keine ID, kein Muster
        print(f"Einen Container „{name}“ gibt es nicht.", file=sys.stderr)
        return 3
    if _gesperrt(name, bekannt[name]):
        print(f"Container „{name}“ ist für die Werkbank gesperrt (Immich bzw. Fridays eigener Stack).", file=sys.stderr)
        return 4
    _vermerken(aktion, name)
    if aktion == "logs":
        zeilen = rest[1] if len(rest) > 1 and rest[1].isdigit() else "100"
        return _ausgeben(_docker("logs", "--tail", str(min(int(zeilen), 2000)), name))
    if aktion == "inspect":
        return _ausgeben(_docker("inspect", name))
    if aktion in ("start", "stop", "restart"):
        return _ausgeben(_docker(aktion, name))
    befehl = rest[1:]                                             # exec
    if not befehl:
        print("exec braucht einen Befehl.", file=sys.stderr)
        return 2
    return _ausgeben(_docker("exec", name, *befehl))


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except subprocess.TimeoutExpired:
        print(f"Zeitlimit von {ZEITLIMIT_S} s überschritten.", file=sys.stderr)
        sys.exit(124)
