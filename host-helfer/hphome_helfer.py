"""hphome-Helfer — der Server-Teil des Tabs „hphome“ (Extras), läuft auf hphome selbst.

Friday steckt gehärtet im Container (ohne Docker-Zugriff). Alles, was den Host betrifft, macht dieser
Helfer, wie `claude_helfer.py` es für tmux tut: Er schreibt alle paar Sekunden den Zustand nach
`data/hphome/zustand.json` und führt Aufträge aus `data/hphome/auftrag.json` aus.

**Der Helfer kennt genau acht Aktionen:** `neustart`, `stopp`, `start`, `logs` (je Container),
`host_neustart`, `platz_schaffen`, `handy_start` (startet die Handy-Steuerung, die sich nach 20 s ohne
Verbindung selbst beendet) und `bot_modell` (stellt den Minecraft-Bot auf Gemini oder DeepSeek um: schreibt
das Modell ins Mindcraft-Profil, legt den DeepSeek-Schlüssel in die Schlüsseldatei DES BOTS und startet den
Container neu). Der Containername muss dem Muster entsprechen UND in `docker ps -a` vorkommen; alle Kommandos
laufen als Argumentliste ohne Shell und mit Zeitlimit. Aus der Auftragsdatei kommt nichts, was als Befehl
ausgeführt wird — beim Modell ist der Wert auf `gemini`/`deepseek` beschränkt, der Schlüssel wird NIE aus dem
Auftrag genommen, sondern aus `~/.config/friday/deepseek.key` gelesen.

Start von Hand (zum Prüfen):  python3 host-helfer/hphome_helfer.py
Dauerbetrieb: host-helfer/hphome_helfer_starten.sh (cron, tmux-Sitzung `friday-hphome-helfer`).
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import stat
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

NAME_OK = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
TAKT_S = 2.0            # so oft wird nach Aufträgen gesehen
ZUSTAND_S = 10.0        # so oft wird der Grundzustand neu gelesen
KURZ_S = 30.0           # docker stats, Tailnet, Sicherung
STUNDE_S = 3600.0       # apt, größte Ordner, Docker-Platz
MIN_ABSTAND_S = 3.0     # zwischen zwei Aktionen
WARTUNG_S = 600         # nach dem Host-Neustart schweigt die Wache so lange
LOGZEILEN = 100
LOG_MAX_ZEICHEN = 20000
AUFTRAG_MAX = 4096      # größer darf eine Auftragsdatei nicht sein
ZUSTAND_MAX = 2 * 1024 * 1024   # Zustandsdateien, die der Container mitschreiben kann
NICHT_STOPPEN = {"friday"}
# Der Minecraft-Bot (Mindcraft) liegt als eigener Stack neben Friday: ~/docker/friday-mc/mindcraft.
# Der Ordnername ist relativ zu ~/docker; für Tests lässt er sich umbiegen, der Container ebenso.
MINDCRAFT_ORDNER = "friday-mc/mindcraft"
BOT_CONTAINER = "friday-mc-mindcraft-1"
# Modellname im Mindcraft-Profil. Gemini ist der bisherige Stand, DeepSeek läuft über Eikes vorhandenen Schlüssel.
MODELLE = {"gemini": "gemini-3.1-flash-lite", "deepseek": "deepseek-flash"}
AKTIONEN = {"neustart", "stopp", "start", "logs", "host_neustart", "platz_schaffen", "handy_start", "bot_modell"}
SUDOERS_ZEILE = "nesreduel ALL=(root) NOPASSWD: /usr/bin/systemctl reboot"
EINRICHTEN = (f"echo '{SUDOERS_ZEILE}' | sudo tee /etc/sudoers.d/friday-hphome && "
              "sudo chmod 440 /etc/sudoers.d/friday-hphome && sudo visudo -c")


def daten_ordner() -> Path:
    return Path(os.getenv("FRIDAY_DATEN", "") or (Path(__file__).resolve().parent.parent / "data"))


PROC_FD = "/proc/self/fd"
_GEPINNT: dict[str, int] = {}     # data/hphome → offener Ordner-fd (einer je Datenordner)
_getauscht_gemeldet: set[str] = set()


def ordner() -> Path:
    """`data/hphome` — als Pfad über den EINMAL geöffneten Ordner-fd (`/proc/self/fd/<fd>`). Der Pfad hängt am
    Ordner selbst, nicht an seinem Namen: Tauscht der Container (gleiche uid) `data/hphome` später gegen einen
    Symlink, landet trotzdem nichts außerhalb. Ob der Name noch auf den Ordner zeigt, prüft `_ordner_pruefen`."""
    p = daten_ordner() / "hphome"
    fd = _GEPINNT.get(str(p))
    if fd is None:
        if p.is_symlink():                    # der Container schreibt hier mit: nie einem Link folgen
            raise OSError("data/hphome ist ein Symlink")
        p.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)   # ein Link hier: ELOOP
        if not os.path.isdir(f"{PROC_FD}/{fd}"):
            os.close(fd)
            raise OSError(f"{PROC_FD} fehlt — der hphome-Helfer läuft nur unter Linux mit /proc")
        _GEPINNT[str(p)] = fd
    return Path(f"{PROC_FD}/{fd}")


def _ordner_schliessen() -> None:
    """Schließt alle gepinnten Ordner (für Tests; der Dauerbetrieb hält den fd bis zum Ende)."""
    for fd in _GEPINNT.values():
        try:
            os.close(fd)
        except OSError:
            pass
    _GEPINNT.clear()


def _ordner_pruefen() -> bool:
    """Zeigt `data/hphome` noch auf den gepinnten Ordner? Sonst (getauscht, verschoben) tut diese Runde nichts;
    neu gepinnt wird nur ein echtes Verzeichnis (oder ein neu angelegtes, wenn der Name ganz fehlt) — nie ein Link."""
    p = daten_ordner() / "hphome"
    fd = _GEPINNT.get(str(p))
    if fd is None:
        ordner()                              # erstes Mal: pinnen (wirft bei einem Symlink)
        return True
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        st = None
    f = os.fstat(fd)
    if st is not None and (st.st_dev, st.st_ino) == (f.st_dev, f.st_ino):
        _getauscht_gemeldet.discard(str(p))
        return True
    if str(p) not in _getauscht_gemeldet:
        _getauscht_gemeldet.add(str(p))
        print(f"[hphome-helfer] {p} wurde getauscht oder verschoben — diese Runde nichts tun", flush=True)
    if st is None or stat.S_ISDIR(st.st_mode):  # lstat: ein Symlink ist nie S_ISDIR
        del _GEPINNT[str(p)]
        os.close(fd)
        try:
            ordner()
        except OSError:
            pass
    return False


def _eltern_oeffnen(pfad: Path) -> int:
    """Öffnet einen Ordner ohne Symlink am Ende. Der gepinnte Ordner (`/proc/self/fd/<fd>`) ist selbst ein
    magischer Link — ihn öffnet `.` relativ zum fd, statt ihm per Pfad zu folgen."""
    for fd in _GEPINNT.values():
        if str(pfad) == f"{PROC_FD}/{fd}":
            return os.open(".", _ORDNER_AUF, dir_fd=fd)
    return os.open(str(pfad), _ORDNER_AUF)


def ausfuehren(befehl: list[str], zeitlimit: float = 20) -> tuple[int, str, str]:
    """(Rückgabewert, stdout, stderr). Wirft nie: ein fehlendes Programm oder ein Zeitüberschritt ist rc 127."""
    try:
        p = subprocess.run(befehl, capture_output=True, text=True, timeout=zeitlimit)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return p.returncode, p.stdout or "", p.stderr or ""


# ---------------------------------------------------------------- kleine Helfer

_EINHEIT = {"b": 1, "kb": 1e3, "kib": 1024, "mb": 1e6, "mib": 1024 ** 2,
            "gb": 1e9, "gib": 1024 ** 3, "tb": 1e12, "tib": 1024 ** 4}


def _bytes(text) -> float:
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", str(text or ""))
    if not m:
        return 0.0
    try:
        return float(m.group(1)) * _EINHEIT.get(m.group(2).lower(), 0)
    except ValueError:
        return 0.0


def _epoch(text) -> int | None:
    if not text or str(text).startswith("0001"):
        return None
    try:
        return int(datetime.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def _sicher_lesen(pfad: Path, grenze: int) -> bytes | None:
    """Höchstens `grenze` Bytes einer GEWÖHNLICHEN Datei. Symlink, FIFO, Ordner, Gerät oder zu groß: None.
    O_NONBLOCK, damit ein untergeschobenes FIFO den Helfer nicht festhält; es wird nie gelesen."""
    try:
        fd = os.open(pfad, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as f:
            roh = f.read(grenze + 1)
        return roh if len(roh) <= grenze else None
    except OSError:
        return None
    finally:
        os.close(fd)


def _json_lesen(pfad: Path, standard, grenze: int = ZUSTAND_MAX):
    roh = _sicher_lesen(pfad, grenze)
    if roh is None:
        return standard
    try:
        return json.loads(roh.decode("utf-8"))
    except (ValueError, RecursionError, MemoryError):              # eine vergiftete Datei darf nie die Runde sprengen
        return standard                                            # (UnicodeDecodeError ist ein ValueError)


BAUM_TIEFE_MAX = 200    # so tief wird gelöscht (je Ebene ein offener Ordner); darunter bleibt es beiseite liegen
_ORDNER_AUF = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _ordner_oeffnen(name: str, eltern: int) -> int:
    """Öffnet den Unterordner `name` von `eltern` und gibt ihn frei (700). Nie über einen Symlink: O_NOFOLLOW am
    Eintrag selbst. Fehlt das Leserecht, wird erst das schon geöffnete Objekt (O_PATH) über /proc/self/fd auf 700
    gesetzt — nie ein Pfad, den der Container inzwischen gegen einen Link getauscht haben könnte."""
    try:
        fd = os.open(name, _ORDNER_AUF, dir_fd=eltern)
    except PermissionError:
        griff = os.open(name, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=eltern)
        try:
            os.chmod(f"/proc/self/fd/{griff}", 0o700)
            fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=griff)
        finally:
            os.close(griff)
    try:
        os.fchmod(fd, 0o700)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _baum_entfernen(pfad: Path) -> None:
    """Löscht einen Ordnerbaum, auch mit entzogenen Rechten. Ohne Rekursion und nur über Ordner-Deskriptoren
    (openat/unlinkat mit dir_fd, O_NOFOLLOW): Folgt nie einem Symlink, auch nicht, wenn der Container mitten im
    Löschen einen Ordner gegen einen Link tauscht; Links werden selbst entfernt. Tiefer als BAUM_TIEFE_MAX bleibt
    der Rest liegen (er liegt beiseite und blockiert nichts). Best effort, wirft nie, kein fd bleibt offen."""
    oben, stapel = None, []                               # stapel: (fd, noch zu löschende Namen, eigener Name)
    try:
        oben = _eltern_oeffnen(pfad.parent)
        if not stat.S_ISDIR(os.stat(pfad.name, dir_fd=oben, follow_symlinks=False).st_mode):
            os.unlink(pfad.name, dir_fd=oben)
            return
        stapel.append((_ordner_oeffnen(pfad.name, oben), [], pfad.name))
        stapel[-1][1].extend(os.listdir(stapel[-1][0]))
        while stapel:
            fd, namen, eigen = stapel[-1]
            if namen:
                n = namen.pop()
                try:
                    if not stat.S_ISDIR(os.stat(n, dir_fd=fd, follow_symlinks=False).st_mode):
                        os.unlink(n, dir_fd=fd)                   # Datei, Link, FIFO …: unlink folgt nie
                    elif len(stapel) < BAUM_TIEFE_MAX:
                        stapel.append((_ordner_oeffnen(n, fd), [], n))   # erst auf den Stapel, dann auflisten:
                        stapel[-1][1].extend(os.listdir(stapel[-1][0]))  # so wird der fd immer geschlossen
                except Exception:                                 # ein Eintrag, der nicht geht, bleibt liegen
                    pass
                continue
            stapel.pop()
            os.close(fd)
            try:
                os.rmdir(eigen, dir_fd=stapel[-1][0] if stapel else oben)
            except OSError:
                pass
    except Exception:
        pass
    finally:
        for eintrag in stapel:
            try:
                os.close(eintrag[0])
            except OSError:
                pass
        if oben is not None:
            try:
                os.close(oben)
            except OSError:
                pass


def _frei_machen(pfad: Path) -> None:
    """Räumt einen eigenen Dateinamen direkt in `ordner()` frei — auch wenn der Container dort einen Ordner, FIFO
    oder Symlink hingelegt hat. Folgt nie einem Link, fasst nichts außerhalb von `ordner()` an, wirft nie.
    Ein Ordner wird erst beiseite geschoben (Umbenennen braucht nur Schreibrecht am Elternordner), dann so gut
    es geht gelöscht; bleibt er stehen, blockiert er trotzdem nichts mehr."""
    try:
        o = ordner()
        if pfad.parent != o:
            return
        if pfad.is_dir() and not pfad.is_symlink():
            weg = o / f".weg-{secrets.token_hex(6)}"
            os.rename(pfad, weg)
            _baum_entfernen(weg)
        else:
            pfad.unlink(missing_ok=True)
    except Exception:
        pass


def _aufraeumen(alter_s: float = 300.0) -> None:
    """Liegengebliebenes im eigenen Ordner: `.weg-*`-Ordner und alte `*.tmp` (Dateien/Links, nie Ziele). Wirft nie."""
    try:
        o = ordner()
        jetzt = time.time()
        with os.scandir(o) as eintraege:
            liste = list(eintraege)
        for e in liste:
            try:
                st = os.lstat(e.path)
                if e.name.startswith(".weg-") and stat.S_ISDIR(st.st_mode):
                    _baum_entfernen(Path(e.path))
                elif e.name.endswith(".tmp") and not stat.S_ISDIR(st.st_mode) and jetzt - st.st_mtime > alter_s:
                    os.unlink(e.path)
            except Exception:
                continue
    except Exception:
        pass


def _schreiben(pfad: Path, daten) -> None:
    _text_schreiben(pfad, json.dumps(daten, ensure_ascii=False))


def _text_schreiben(pfad: Path, text: str) -> None:
    """Atomar und ohne Symlinks. Der Zwischenname ist zufällig (kein fester Name, den der Container besetzen
    kann) und wird exklusiv angelegt; ein Link oder Ordner am Ziel wird ersetzt bzw. beiseite geschoben."""
    tmp = pfad.with_name(f"{pfad.name}.{secrets.token_hex(4)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if pfad.is_dir() and not pfad.is_symlink():
            _frei_machen(pfad)                # ein Ordner am Ziel ließe das Umbenennen scheitern
        tmp.replace(pfad)                     # rename ersetzt eine Datei oder einen Symlink am Ziel
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _lesen(pfad: str) -> str:
    try:
        return Path(pfad).read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------------------------------------------------------------- Zustand sammeln

def host_info(proc: str = "/proc", thermal: str = "/sys/class/thermal", root: str = "/",
              reboot_datei: str = "/var/run/reboot-required", statvfs=os.statvfs) -> dict:
    up = _lesen(f"{proc}/uptime").split()
    last = _lesen(f"{proc}/loadavg").split()
    mem: dict[str, int] = {}
    for zeile in _lesen(f"{proc}/meminfo").splitlines():
        k, _, v = zeile.partition(":")
        w = v.split()
        if w and w[0].isdigit():
            mem[k] = int(w[0])
    temps = []
    for z in sorted(Path(thermal).glob("thermal_zone*/temp")):
        try:
            t = int(z.read_text().strip()) / 1000
        except (OSError, ValueError):
            continue
        if 0 < t < 150:                       # 0 = tote Zone
            temps.append(t)
    platten = []
    try:
        st = statvfs(root)
        belegt = (st.f_blocks - st.f_bfree) * st.f_frsize
        frei = st.f_bavail * st.f_frsize      # nutzbar, ohne die Reserve für root (wie df „Verf.“)
        platten.append({"pfad": root, "gesamt_gb": round(st.f_blocks * st.f_frsize / 1e9, 1),
                        "belegt_gb": round(belegt / 1e9, 1), "frei_gb": round(frei / 1e9, 1),
                        "prozent": round(belegt / (belegt + frei) * 100, 1) if belegt + frei else 0.0})
    except (OSError, ValueError):
        pass
    try:
        letzte = [float(x) for x in last[:3]] if len(last) >= 3 else None
    except ValueError:
        letzte = None
    return {
        "uptime_s": int(float(up[0])) if up else None,
        "last": letzte,
        "kerne": os.cpu_count(),
        "ram": ({"gesamt_mb": round(mem["MemTotal"] / 1024, 1), "verfuegbar_mb": round(mem["MemAvailable"] / 1024, 1)}
                if "MemTotal" in mem and "MemAvailable" in mem else None),
        "swap": ({"gesamt_mb": round(mem["SwapTotal"] / 1024, 1), "frei_mb": round(mem["SwapFree"] / 1024, 1)}
                 if "SwapTotal" in mem and "SwapFree" in mem else None),
        "platten": platten,
        "temperatur_c": round(max(temps), 1) if temps else None,
        "neustart_noetig": Path(reboot_datei).exists(),
    }


def container_lesen() -> list[dict] | None:
    """Alle Container (auch gestoppte). `None`, wenn Docker nicht antwortet — nicht „keine Container“."""
    rc, aus, _ = ausfuehren(["docker", "ps", "-a", "-q", "--no-trunc"])
    if rc != 0:
        return None
    ids = aus.split()
    if not ids:
        return []
    rc, aus, _ = ausfuehren(["docker", "inspect", *ids], 30)
    if rc != 0:
        return None
    try:
        rohe = json.loads(aus)
    except ValueError:
        return None
    heraus = []
    for c in rohe:
        st = c.get("State") or {}
        ports, gesehen = [], set()
        for schluessel, bindungen in ((c.get("NetworkSettings") or {}).get("Ports") or {}).items():
            for b in bindungen or []:
                try:
                    p = (int(b["HostPort"]), int(schluessel.split("/")[0]))
                except (KeyError, ValueError):
                    continue
                if p not in gesehen:
                    gesehen.add(p)
                    ports.append({"host": p[0], "container": p[1]})
        host = c.get("HostConfig") or {}
        heraus.append({
            "name": str(c.get("Name") or "").lstrip("/"),
            "image": (c.get("Config") or {}).get("Image") or "",
            "zustand": st.get("Status") or "",
            "gesundheit": (st.get("Health") or {}).get("Status") or "",
            "seit": _epoch(st.get("StartedAt")) if st.get("Running") else None,
            "ports": sorted(ports, key=lambda p: p["host"]),
            "host_netz": host.get("NetworkMode") == "host",
            "neustart_regel": (host.get("RestartPolicy") or {}).get("Name") or "",
        })
    return sorted(heraus, key=lambda c: c["name"])


def stats_lesen() -> dict[str, dict]:
    rc, aus, _ = ausfuehren(["docker", "stats", "--no-stream", "--format", "{{json .}}"], 60)
    if rc != 0:
        return {}
    heraus = {}
    for zeile in aus.splitlines():
        try:
            d = json.loads(zeile)
        except ValueError:
            continue
        try:
            cpu = float(str(d.get("CPUPerc", "")).rstrip("%"))
        except ValueError:
            cpu = None
        heraus[d.get("Name", "")] = {"cpu_prozent": cpu,
                                     "ram_mb": round(_bytes(str(d.get("MemUsage", "")).split("/")[0]) / 1024 ** 2, 1)}
    return heraus


def tailnet_lesen() -> dict | None:
    rc, aus, _ = ausfuehren(["tailscale", "status", "--json"], 15)
    if rc != 0:
        return None
    try:
        d = json.loads(aus)
    except ValueError:
        return None
    geraete = []
    eintraege = [(d.get("Self"), True)] + [(p, False) for p in (d.get("Peer") or {}).values()]
    for p, selbst in eintraege:
        if not p:
            continue
        name = (p.get("DNSName") or p.get("HostName") or "").split(".")[0]
        online = True if selbst else bool(p.get("Online"))
        geraete.append({"name": name, "os": p.get("OS") or "", "online": online, "selbst": selbst,
                        "zuletzt": None if online else _epoch(p.get("LastSeen"))})
    geraete.sort(key=lambda g: (not g["online"], not g["selbst"], g["name"]))
    return {"name": d.get("MagicDNSSuffix") or "", "geraete": geraete}


def sicherung_lesen(daten: Path) -> dict | None:
    """Nur gewöhnliche Dateien direkt in `data/sicherung` — nie über einen Symlink (den legt sonst der Container)."""
    try:
        fd = os.open(daten / "sicherung", _ORDNER_AUF)   # Symlink: ELOOP; über den fd auch nicht mehr tauschbar
    except OSError:
        return None
    werte = []
    try:
        for name in os.listdir(fd):
            if name.startswith("."):
                continue
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                werte.append(st)
    except OSError:
        return None
    finally:
        os.close(fd)
    if not werte:
        return None
    return {"zuletzt": int(max(s.st_mtime for s in werte)), "dateien": len(werte),
            "groesse_mb": round(sum(s.st_size for s in werte) / 1024 ** 2, 1)}


def speicher_lesen(home: Path | None = None) -> list[dict]:
    """Die größten Ordner (eine Ebene) im Home, in media/ und in downloads/. Kann dauern — läuft im Hintergrund."""
    home = home or Path.home()
    alle: dict[str, float] = {}
    for wurzel in (home, home / "media", home / "downloads"):
        if not wurzel.is_dir():
            continue
        _, aus, _ = ausfuehren(["du", "-x", "-k", "--max-depth=1", str(wurzel)], 180)   # rc ≠ 0 bei Rechtefehlern, Zeilen gelten trotzdem
        for zeile in aus.splitlines():
            kb, _, pfad = zeile.partition("\t")
            if not kb.isdigit() or pfad == str(wurzel):
                continue
            alle[os.path.relpath(pfad, home)] = round(int(kb) * 1024 / 1e9, 2)
    return [{"pfad": p, "groesse_gb": g} for p, g in sorted(alle.items(), key=lambda x: -x[1])[:8]]


def docker_platz_lesen() -> dict | None:
    rc, aus, _ = ausfuehren(["docker", "system", "df", "--format", "{{json .}}"], 60)
    if rc != 0:
        return None
    werte = {}
    for zeile in aus.splitlines():
        try:
            d = json.loads(zeile)
        except ValueError:
            continue
        werte[d.get("Type")] = _bytes(str(d.get("Reclaimable", "")).split("(")[0])
    return {"build_cache_gb": round(werte.get("Build Cache", 0) / 1e9, 2),
            "images_frei_gb": round(werte.get("Images", 0) / 1e9, 2)}


def updates_lesen() -> int | None:
    """Wie viele apt-Updates anstehen — nach dem lokalen Stand, ohne sudo, nur zur Anzeige."""
    rc, aus, _ = ausfuehren(["apt", "list", "--upgradable"], 60)
    if rc != 0:
        return None
    return sum(1 for z in aus.splitlines() if "[upgradable from" in z)


def _absichtlich_lesen() -> set[str]:
    daten = _json_lesen(ordner() / "absichtlich.json", [])
    return {n for n in daten if isinstance(n, str)} if isinstance(daten, list) else set()


def _wartung_lesen(jetzt: float):
    daten = _json_lesen(ordner() / "wartung.json", {})
    bis = daten.get("bis") if isinstance(daten, dict) else None
    if not isinstance(bis, (int, float)) or not math.isfinite(bis):   # 1e999 = inf: int() würde jede Runde werfen
        return None
    return int(bis) if bis > jetzt else None


class Sammler:
    """Hält die langsamen Werte zwischen den Runden, damit ein Lauf alle 10 s reicht."""

    def __init__(self):
        self.hintergrund = True          # Tests stellen das ab
        self._werte: dict = {}
        self._zeit: dict = {}
        self._laeuft: set[str] = set()

    def vergessen(self, name: str) -> None:
        self._zeit.pop(name, None)

    def _holen(self, name: str, holen) -> None:
        try:
            self._werte[name] = holen()
        except Exception as exc:                                   # ein Werk darf den Rest nicht kippen
            print(f"[hphome-helfer] {name}: {exc}", flush=True)
        finally:
            self._laeuft.discard(name)

    def _frisch(self, name: str, alle_s: float, holen, jetzt: float, langsam: bool = False):
        if (name not in self._zeit or jetzt - self._zeit[name] >= alle_s) and name not in self._laeuft:
            self._zeit[name] = jetzt
            self._laeuft.add(name)
            if langsam and self.hintergrund:
                threading.Thread(target=self._holen, args=(name, holen), daemon=True).start()
            else:
                self._holen(name, holen)
        return self._werte.get(name)

    def zustand(self, jetzt: float | None = None) -> dict:
        jetzt = time.time() if jetzt is None else jetzt
        host = host_info()
        host["updates"] = self._frisch("updates", STUNDE_S, updates_lesen, jetzt, langsam=True)
        container = container_lesen()
        stats = self._frisch("stats", KURZ_S, stats_lesen, jetzt, langsam=True) or {}
        absichtlich = _absichtlich_lesen()
        laufend = {c["name"] for c in container or [] if c["zustand"] == "running"}
        if absichtlich & laufend:
            absichtlich -= laufend
            _schreiben(ordner() / "absichtlich.json", sorted(absichtlich))
        for c in container or []:
            c.update(stats.get(c["name"]) or {"cpu_prozent": None, "ram_mb": None})
            c["absichtlich_gestoppt"] = c["name"] in absichtlich and c["zustand"] != "running"
        return {
            "helfer": True,
            "aktualisiert": int(jetzt),
            "host": host,
            "container": container if container is not None else [],
            "container_ok": container is not None,
            "mindcraft": {"modell": mindcraft_modell()},
            "tailnet": self._frisch("tailnet", KURZ_S, tailnet_lesen, jetzt),
            "sicherung": self._frisch("sicherung", KURZ_S, lambda: sicherung_lesen(daten_ordner()), jetzt),
            "speicher": {"ordner": self._frisch("ordner", STUNDE_S, speicher_lesen, jetzt, langsam=True) or [],
                         "docker": self._frisch("docker", STUNDE_S, docker_platz_lesen, jetzt, langsam=True)},
            "wartung_bis": _wartung_lesen(jetzt),
        }


# ---------------------------------------------------------------- Aktionen

def _verlauf(aktion: str, name: str, ok: bool, text: str) -> None:
    pfad = ordner() / "verlauf.jsonl"
    name = name if NAME_OK.fullmatch(name) else ""      # nur ein geprüfter Name kommt in den Verlauf
    zeile = json.dumps({"zeit": int(time.time()), "aktion": aktion, "name": name, "ok": ok, "text": text[:200]},
                       ensure_ascii=False)
    try:
        # Symlink → ELOOP, FIFO ohne Leser → ENXIO (O_NONBLOCK), Ordner → EISDIR: dann kein Verlauf.
        fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return
            os.write(fd, (zeile + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        roh = _sicher_lesen(pfad, ZUSTAND_MAX)
        if roh is None:                                  # über der Grenze (gewöhnliche Datei, s. o.): neu beginnen
            _text_schreiben(pfad, zeile + "\n")
            return
        zeilen = roh.decode("utf-8").splitlines()
        if len(zeilen) > 1000:
            _text_schreiben(pfad, "\n".join(zeilen[-500:]) + "\n")
    except (OSError, ValueError):
        pass                                             # der Verlauf ist Beiwerk, nie ein Grund abzubrechen


def _name_pruefen(name) -> str | None:
    """Einen Fehlertext, wenn der Name nicht geht — sonst None. Docker wird nur nach der Namensliste gefragt."""
    if not isinstance(name, str) or not NAME_OK.fullmatch(name):
        return "Ungültiger Containername."
    rc, aus, _ = ausfuehren(["docker", "ps", "-a", "--format", "{{.Names}}"])
    if rc != 0:
        return "Docker antwortet nicht."
    if name not in aus.split():
        return f"Einen Container „{name}“ gibt es nicht."
    return None


def container_aktion(aktion: str, name: str) -> tuple[bool, str]:
    docker_befehl = {"neustart": "restart", "stopp": "stop", "start": "start"}.get(aktion)
    if docker_befehl is None:
        return False, "Unbekannte Aktion."
    fehler = _name_pruefen(name)
    if fehler:
        return False, fehler
    if aktion == "stopp" and name in NICHT_STOPPEN:
        return False, f"„{name}“ lässt sich hier nicht stoppen — sonst gäbe es keine Oberfläche mehr, um es zu starten."
    rc, aus, err = ausfuehren(["docker", docker_befehl, name], 60)
    if rc != 0:
        return False, f"docker {docker_befehl} scheiterte: {(err or aus).strip()[:200]}"
    absichtlich = _absichtlich_lesen()
    if aktion == "stopp":
        absichtlich.add(name)
    else:
        absichtlich.discard(name)
    _schreiben(ordner() / "absichtlich.json", sorted(absichtlich))
    return True, {"neustart": f"„{name}“ wurde neu gestartet.", "stopp": f"„{name}“ wurde gestoppt.",
                  "start": f"„{name}“ wurde gestartet."}[aktion]


def logs_holen(name: str) -> tuple[bool, str]:
    fehler = _name_pruefen(name)
    if fehler:
        return False, fehler
    rc, aus, err = ausfuehren(["docker", "logs", "--tail", str(LOGZEILEN), name], 20)
    if rc != 0:
        return False, f"Logs nicht lesbar: {err.strip()[:200]}"
    # Was ein Container auf stdout und stderr schreibt, kommt getrennt zurück.
    return True, (aus + err).strip()[-LOG_MAX_ZEICHEN:] or "(keine Zeilen)"


def host_neustart() -> tuple[bool, str, bool]:
    """(ok, Text, danach_ausfuehren). Der Neustart selbst läuft erst NACH der Antwort (siehe `lauf`)."""
    rc, _, _ = ausfuehren(["sudo", "-n", "-l", "/usr/bin/systemctl", "reboot"], 10)
    if rc != 0:
        return False, "Der Neustart ist noch nicht eingerichtet. Einmal im Terminal ausführen: " + EINRICHTEN, False
    _schreiben(ordner() / "wartung.json", {"bis": int(time.time()) + WARTUNG_S})
    return True, "hphome startet neu — alle Dienste sind etwa zwei Minuten weg.", True


def platz_schaffen() -> tuple[bool, str]:
    """Nur Build-Cache und Images, die kein Container braucht und die älter als 24 h sind. Nie Volumes."""
    insgesamt = 0.0
    for befehl in (["docker", "builder", "prune", "-f", "--filter", "until=24h"],
                   ["docker", "image", "prune", "-a", "-f", "--filter", "until=24h"]):
        rc, aus, err = ausfuehren(befehl, 300)
        if rc != 0:
            return False, f"Aufräumen scheiterte: {(err or aus).strip()[:200]}"
        for m in re.finditer(r"Total(?: reclaimed space)?:\s*([\d.]+\s*[A-Za-z]+)", aus):
            insgesamt += _bytes(m.group(1))
    return True, f"Aufgeräumt: {insgesamt / 1e9:.2f} GB frei geworden."


def handy_start() -> tuple[bool, str]:
    """Schaltet die Handy-Steuerung an (feste Skriptdatei, keine Eingabe aus dem Auftrag). Der Dienst beendet sich selbst, wenn
    nicht binnen 20 s eine Seite verbunden ist — solange er läuft, hält er das Handy per adb und Snapchat bleibt blockiert."""
    skript = Path(__file__).resolve().parent / "handy_steuerung_starten.sh"
    rc, aus, err = ausfuehren(["bash", str(skript)], 30)
    if rc != 0:
        return False, f"Die Handy-Steuerung ließ sich nicht starten: {(err or aus).strip()[:200]}"
    return True, "Handy-Steuerung läuft — jetzt öffnen (ohne Verbindung beendet sie sich nach 20 Sekunden)."


# ---------------------------------------------------------------- Minecraft-Bot (Modell)

def mindcraft_ordner() -> Path:
    """Der Mindcraft-Ordner des Bots — normalerweise `~/docker/friday-mc/mindcraft` neben `friday-web`."""
    fest = os.getenv("MINDCRAFT_ORDNER", "").strip()
    if fest:
        return Path(fest)
    return Path(__file__).resolve().parents[1].parent / MINDCRAFT_ORDNER


def mindcraft_modell() -> str:
    """`deepseek`, `gemini` oder "" — was im Profil des Bots steht (nur zur Anzeige, nie aus dem Auftrag)."""
    profil = _json_lesen(mindcraft_ordner() / "profiles" / "friday.json", {})
    modell = profil.get("model") if isinstance(profil, dict) else None
    if not isinstance(modell, str) or not modell.strip():
        return ""
    modell = modell.strip().lower()
    return "deepseek" if "deepseek" in modell else ("gemini" if "gemini" in modell else "")


def deepseek_schluessel() -> str:
    """Eikes DeepSeek-Schlüssel aus der Datei auf dem Host. NUR von dort — nie aus dem Auftrag, nie in der Antwort."""
    datei = Path(os.getenv("DEEPSEEK_KEY_DATEI", "").strip()
                 or Path.home() / ".config/friday/deepseek.key")
    try:
        wert = datei.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return wert if wert.startswith("sk-") else ""


def _json_schreiben(pfad: Path, daten: dict, modus: int) -> None:
    """JSON atomar schreiben (temporäre Datei daneben, dann ersetzen) und nie einem Symlink folgen. Die Rechte
    werden ausdrücklich gesetzt — die Schlüsseldatei bleibt 600, das Profil 644."""
    text = json.dumps(daten, ensure_ascii=False, indent=4) + "\n"
    tmp = pfad.with_name(f".{pfad.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, modus)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, modus)
        os.replace(tmp, pfad)
    except OSError:
        _frei_machen(tmp)
        raise


def bot_modell(wert: str) -> tuple[bool, str]:
    """Stellt den Minecraft-Bot auf Gemini oder DeepSeek um: Modell ins Profil, DeepSeek-Schlüssel in die
    Schlüsseldatei des Bots, dann den Container neu starten. Der Wert ist eine Positivliste, der Schlüssel kommt
    aus der Datei auf dem Host. Andere Profilfelder bleiben unverändert."""
    modell = str(wert or "").strip().lower()
    if modell not in MODELLE:
        return False, "Unbekanntes Modell — erlaubt sind gemini und deepseek."
    ordner_mc = mindcraft_ordner()
    profil_datei = ordner_mc / "profiles" / "friday.json"
    keys_datei = ordner_mc / "keys.json"
    profil = _json_lesen(profil_datei, None)
    if not isinstance(profil, dict) or not profil:
        return False, "Das Profil des Bots fehlt oder ist unlesbar — nichts geändert."
    keys = _json_lesen(keys_datei, None)
    if not isinstance(keys, dict):
        return False, "Die Schlüsseldatei des Bots fehlt oder ist unlesbar — nichts geändert."
    schluessel = ""
    if modell == "deepseek":
        schluessel = deepseek_schluessel()
        if not schluessel:
            return False, "Kein DeepSeek-Schlüssel hinterlegt (Datei ~/.config/friday/deepseek.key fehlt) — nichts geändert."
    neu = {**profil, "model": MODELLE[modell], "code_model": MODELLE[modell]}
    schon = (profil.get("model") == MODELLE[modell] and profil.get("code_model") == MODELLE[modell]
             and (modell != "deepseek" or keys.get("DEEPSEEK_API_KEY") == schluessel))
    try:
        if modell == "deepseek" and keys.get("DEEPSEEK_API_KEY") != schluessel:
            _json_schreiben(keys_datei, {**keys, "DEEPSEEK_API_KEY": schluessel}, 0o600)
        if not schon:
            _json_schreiben(profil_datei, neu, 0o644)
    except OSError:
        return False, "Die Dateien des Bots ließen sich nicht schreiben — nichts geändert."
    ok, text = container_aktion("neustart", BOT_CONTAINER)
    if not ok:
        return False, f"Modell gespeichert, aber der Bot startet nicht neu: {text}"
    wort = "DeepSeek" if modell == "deepseek" else "Gemini"
    return True, (f"Der Bot läuft jetzt auf {wort} und wurde neu gestartet."
                  if not schon else f"Der Bot lief schon auf {wort} und wurde neu gestartet.")


_letzte = {"zeit": 0.0}


def auftrag_ausfuehren(auftrag: dict, sammler: Sammler | None = None) -> tuple[dict, bool]:
    aktion = auftrag.get("aktion") if isinstance(auftrag.get("aktion"), str) else ""
    name = auftrag.get("name") if isinstance(auftrag.get("name"), str) else ""
    if len(name) > 63:                        # nicht kürzen: ein zu langer Name ist ein ungültiger Name
        name = ""
    # Beim Modellwechsel steht der gewünschte Wert hier — eine kurze Positivliste, nie ein Pfad oder Befehl.
    wert = auftrag.get("wert") if isinstance(auftrag.get("wert"), str) else ""
    if len(wert) > 63:
        wert = ""
    danach = False
    jetzt = time.monotonic()
    if aktion not in AKTIONEN:
        ok, text = False, "Unbekannte Aktion."
    elif jetzt - _letzte["zeit"] < MIN_ABSTAND_S:
        ok, text = False, "Bitte einen Moment warten, dann noch einmal."
    else:
        _letzte["zeit"] = jetzt
        if aktion == "logs":
            ok, text = logs_holen(name)
        elif aktion in ("neustart", "stopp", "start"):
            ok, text = container_aktion(aktion, name)
        elif aktion == "host_neustart":
            ok, text, danach = host_neustart()
        elif aktion == "handy_start":
            ok, text = handy_start()
        elif aktion == "bot_modell":
            ok, text = bot_modell(wert)
        else:
            ok, text = platz_schaffen()
            if sammler is not None:
                sammler.vergessen("docker")
        if aktion != "logs":                  # Logs können Geheimnisse enthalten: nie in den Verlauf
            _verlauf(aktion, name, ok, text)  # beim Modellwechsel steht dort nur der Name des Modells, nie der Schlüssel
    return {"id": re.sub(r"[^A-Za-z0-9_-]", "", str(auftrag.get("id") or ""))[:64], "ok": ok, "text": text, "zeit": int(time.time())}, danach


def _auftrag_holen(auftrag_datei: Path, arbeit_datei: Path) -> dict | None:
    """Übernimmt einen Auftrag: erst umbenennen (nimmt einen Symlink, FIFO oder Ordner selbst mit, folgt nie),
    dann höchstens 4 KiB einer gewöhnlichen Datei lesen. Alles, was kein kleines JSON-Objekt ist, wird verworfen.
    Die Arbeitsdatei verschwindet immer — eine vergiftete Datei darf den Helfer nie festhalten. Wirft nie."""
    try:
        if not os.path.lexists(auftrag_datei):
            return None
        _frei_machen(arbeit_datei)                 # ein liegengebliebener Ordner/Link dort würde das Umbenennen sprengen
        try:
            os.replace(auftrag_datei, arbeit_datei)
        except OSError:                            # z. B. Ordner am Ziel, den wir nicht wegbekommen: Auftrag verwerfen
            _frei_machen(auftrag_datei)
            return None
        try:
            roh = _sicher_lesen(arbeit_datei, AUFTRAG_MAX)
            if roh is None:
                return None
            auftrag = json.loads(roh.decode("utf-8"))
            return auftrag if isinstance(auftrag, dict) and auftrag else None
        except Exception:                          # kein UTF-8, kein JSON, zu tief verschachtelt …
            return None
        finally:
            _frei_machen(arbeit_datei)
    except Exception:
        return None


def _reboot_gescheitert(rc: int, aus: str, err: str) -> None:
    """Der eigentliche Neustart schlug fehl (trotz bestandener `-l`-Prüfung): Wartung zurücknehmen, damit die Wache
    nicht grundlos schweigt, und es im Verlauf festhalten."""
    _frei_machen(ordner() / "wartung.json")
    _verlauf("host_neustart", "", False, f"Neustart-Befehl scheiterte: {(err or aus).strip()[:150] or f'rc {rc}'}")


def lauf(takt: float = TAKT_S, runden: int | None = None, sammler: Sammler | None = None) -> None:
    sammler = sammler or Sammler()
    # Die letzte Antwort überlebt einen Neustart des Helfers: Friday holt sie vielleicht erst Sekunden später ab.
    alt = _json_lesen(ordner() / "zustand.json", {})
    letzte_antwort = (alt.get("letzte_antwort") if isinstance(alt, dict) else None) or {}
    zuletzt, grund = 0.0, {}
    runde = 0
    while runden is None or runde < runden:
        runde += 1
        try:
            if _ordner_pruefen():                                  # getauscht/verschoben: diese Runde nichts tun
                # Pfade jede Runde neu über den gepinnten Ordner (nie über den Namen `data/hphome`)
                o = ordner()
                auftrag_datei, zustand_datei, arbeit_datei = o / "auftrag.json", o / "zustand.json", o / "auftrag.arbeit"
                danach = False
                try:                                               # Aufräumen darf das Zustandsschreiben nie überspringen
                    _aufraeumen()
                except Exception as exc:
                    print(f"[hphome-helfer] Aufräumen: {exc}", flush=True)
                try:                                               # ein Fehler beim Auftrag darf das Zustandsschreiben nie überspringen
                    auftrag = _auftrag_holen(auftrag_datei, arbeit_datei)
                    if auftrag:
                        letzte_antwort, danach = auftrag_ausfuehren(auftrag, sammler)
                        zuletzt = 0.0                              # gleich neu lesen: die Seite soll das Ergebnis sehen
                except Exception as exc:
                    print(f"[hphome-helfer] Auftrag: {exc}", flush=True)
                if time.monotonic() - zuletzt >= ZUSTAND_S or zuletzt == 0.0:
                    try:                                           # ein werfender Sammler: der letzte gute Zustand bleibt,
                        grund = sammler.zustand()                  # Antwort und „aktualisiert“ gehen trotzdem raus
                    except Exception as exc:
                        print(f"[hphome-helfer] Zustand: {exc}", flush=True)
                    zuletzt = time.monotonic()
                _schreiben(zustand_datei, {**grund, "aktualisiert": int(time.time()), "letzte_antwort": letzte_antwort})
                if danach:
                    time.sleep(2)                                  # Friday holt die Antwort ab (Takt 0,25 s)
                    rc, aus, err = ausfuehren(["sudo", "-n", "/usr/bin/systemctl", "reboot"], 20)
                    if rc != 0:
                        _reboot_gescheitert(rc, aus, err)
        except Exception as exc:                                   # der Helfer darf nie sterben
            print(f"[hphome-helfer] Fehler: {exc}", flush=True)
        if runden is None or runde < runden:
            time.sleep(takt)


if __name__ == "__main__":
    os.umask(0o077)
    print(f"[hphome-helfer] läuft, Aufträge aus {daten_ordner() / 'hphome'} (fd {ordner()})", flush=True)
    lauf()
