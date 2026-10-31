"""Server-Werkzeug — Auskunft über hphome (Platte, Auslastung, Dienste, Tailnet, Sicherung).

Nur lesend, und ohne Logs: Was Container schreiben, kann fremder Text sein und gehört nie ans Modell.
Neustart, Stopp, Start, Host-Neustart und Aufräumen gibt es hier nicht — nur Eikes Tipp in der App
(`kern/dienste/hphome.py`). Am Konnektor „hphome“, standardmäßig aus.
"""

from __future__ import annotations

import re

import time

from kern.dienste import hphome as dienst

KONNEKTOR = "hphome"

ERKLAERUNGEN = [{
    "name": "server_status",
    "description": ("Auskunft über den Server hphome, nur lesend: wie voll die Platte ist, Auslastung und Laufzeit, welche "
                    "Dienste (Jellyfin, Sonarr, Radarr …) laufen oder gestoppt sind, welche Geräte im Tailnet online sind, "
                    "wann die letzte Sicherung war. Benutzen bei Fragen wie „Wie voll ist die Platte?“, „Läuft Jellyfin?“, "
                    "„Ist mit dem Server alles in Ordnung?“. Ändern kann dieses Werkzeug nichts; Neustart und Aufräumen "
                    "macht Eike selbst in der App (Extras → hphome)."),
    "parameters": {"type": "OBJECT", "properties": {
        "was": {"type": "STRING",
                "description": "'alles' (Standard), 'platte', 'dienste', 'tailnet' oder 'sicherung'"},
    }},
}]


def _gb(x) -> str:
    return "?" if x is None else (f"{x:.0f} GB" if x >= 10 else f"{x:.1f} GB")


def _dauer(s) -> str:
    if s is None:
        return "unbekannt"
    t, h, m = int(s // 86400), int(s % 86400 // 3600), int(s % 3600 // 60)
    return f"{t} Tage {h} h" if t else f"{h} h {m} min" if h else f"{m} min"


def _vor(ts) -> str:
    if not ts:
        return "unbekannt"
    s = max(0, time.time() - ts)
    return f"vor {int(s // 86400)} Tagen" if s >= 86400 else f"vor {int(s // 3600)} h" if s >= 3600 else f"vor {int(s // 60)} min"


def _system(z: dict) -> str:
    h = z.get("host") or {}
    zeilen = [f"hphome läuft seit {_dauer(h.get('uptime_s'))}."]
    if h.get("last"):
        zeilen.append(f"Last {h['last'][0]:.2f} ({h.get('kerne') or '?'} Kerne).")
    if h.get("ram"):
        zeilen.append(f"Arbeitsspeicher: {h['ram']['verfuegbar_mb'] / 1024:.1f} von {h['ram']['gesamt_mb'] / 1024:.1f} GB frei.")
    if h.get("temperatur_c") is not None:
        zeilen.append(f"Temperatur {h['temperatur_c']:.0f} °C.")
    if h.get("neustart_noetig"):
        zeilen.append("Ein Neustart ist nötig (Updates warten darauf).")
    if h.get("updates"):
        zeilen.append(f"{h['updates']} Updates stehen an.")
    return " ".join(zeilen)


def _platte(z: dict) -> str:
    zeilen = []
    for p in (z.get("host") or {}).get("platten") or []:
        # Nutzbarer Platz vom Helfer (ohne root-Reserve, wie df); gesamt − belegt wäre bis zu 5 % zu viel.
        frei = p.get("frei_gb")
        zeilen.append(f"Platte {p.get('pfad', '/')}: {_gb(p.get('belegt_gb'))} von {_gb(p.get('gesamt_gb'))} belegt "
                      f"({p.get('prozent', 0):.0f} %), {_gb(frei if isinstance(frei, (int, float)) else None)} frei.")
    if not zeilen:
        zeilen.append("Zur Platte liegt gerade nichts vor.")
    s = z.get("speicher") or {}
    if s.get("ordner"):
        zeilen.append("Größte Ordner: " + ", ".join(f"{o['pfad']} {_gb(o['groesse_gb'])}" for o in s["ordner"][:5]) + ".")
    d = s.get("docker")
    if d:
        zeilen.append(f"Docker könnte etwa {_gb((d.get('build_cache_gb') or 0) + (d.get('images_frei_gb') or 0))} freigeben.")
    return "\n".join(zeilen)


def _dienste(z: dict) -> str:
    if not z.get("container_ok", True):
        return "Docker antwortet nicht — zu den Diensten kann ich gerade nichts sagen."
    c = z.get("container") or []
    if not c:
        return "Es sind keine Container bekannt."
    laufen = [x for x in c if x.get("zustand") == "running"]
    aus = [x["name"] for x in c if x.get("zustand") != "running"]
    krank = [x["name"] for x in laufen if x.get("gesundheit") == "unhealthy"]
    text = f"{len(laufen)} von {len(c)} Diensten laufen: {', '.join(x['name'] for x in laufen) or 'keiner'}."
    if aus:
        text += " Gestoppt: " + ", ".join(aus) + "."
    if krank:
        text += " Nicht gesund: " + ", ".join(krank) + "."
    if not aus and not krank:
        text += " Alle sind in Ordnung."
    return text


_HOSTNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,31}")


def _tailnet(z: dict) -> str:
    t = z.get("tailnet")
    if not t:
        return "Zum Tailnet liegt gerade nichts vor."
    geraete = t.get("geraete") or []
    on = [g for g in geraete if g.get("online")]
    off = [g for g in geraete if not g.get("online")]
    text = f"Im Tailnet {'ist' if len(on) == 1 else 'sind'} {len(on)} {'Gerät' if len(on) == 1 else 'Geräte'} online, {len(off)} offline."

    # Gerätenamen vergibt jeder im Tailnet selbst (fremder Text): nur kurze Hostnamen gehen wörtlich ans Modell, alles andere
    # wird gezählt und anonym genannt (28.10.2026: ohne Namen schloss Friday aus „5 online, 0 offline“ auf „mchome nicht erreichbar“).
    def namen(liste: list[dict]) -> str:
        gut = [str(g.get("name")) for g in liste if _HOSTNAME.fullmatch(str(g.get("name") or ""))]
        rest = len(liste) - len(gut)
        return ", ".join(gut + ([f"{rest} mit ungewöhnlichem Namen"] if rest else []))

    if on:
        text += f" Online: {namen(on)}."
    if off:
        text += f" Offline: {namen(off)}."
    return text


def _sicherung(z: dict) -> str:
    s = z.get("sicherung")
    if not s:
        return "Zur Sicherung liegt gerade nichts vor."
    return f"Sicherung von Friday: zuletzt {_vor(s.get('zuletzt'))}, {s.get('dateien')} Dateien, {s.get('groesse_mb')} MB."


def server_status(was: str = "alles") -> str:
    z = dienst.zustand_roh()
    if z is None:
        return "Der Helfer auf hphome meldet sich gerade nicht — ich kann den Serverzustand nicht sagen."
    was = (was or "alles").strip().lower()
    teile = {"platte": _platte, "dienste": _dienste, "tailnet": _tailnet, "sicherung": _sicherung}
    if was in teile:
        return teile[was](z)
    if was != "alles":
        return "Ich kenne: alles, platte, dienste, tailnet, sicherung."
    return "\n".join([_system(z), _platte(z), _dienste(z), _sicherung(z)])


WERKZEUGE = {"server_status": server_status}
