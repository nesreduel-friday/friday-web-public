"""Eikes Windows-PC — über den Helfer `pc-helfer/friday_pc.py`, der auf dem PC läuft.

Friday spricht nur mit diesem Helfer (im Tailnet, mit Schlüssel), nie direkt mit
Windows. Der Helfer kennt eine feste Liste von Aktionen und stuft jede selbst ein:
`sicher`, `bestaetigen` oder `verboten` (siehe dort).

**Heikles bestätigt Eike in der App, nicht im Gespräch** (Eikes Wunsch, 21.09.2026).
Ein Werkzeug legt dafür hier eine Rückfrage ab (`vormerken`); die App zeigt sie als
Karte mit „Ausführen“ und „Ablehnen“, und nur `entscheiden` — aufgerufen von der
Route, die Eikes Tipp empfängt — schickt die Aktion mit `bestaetigt` an den PC.
Das Modell hat keinen Weg dorthin: Kein Werkzeug ruft `entscheiden` auf, und der
Helfer führt Heikles ohne `bestaetigt` nicht aus (doppelter Riegel).

Die Rückfrage trägt den Fingerabdruck aus der Prüfung (Größe und Zeit der Datei,
Knopf und Seite im Browser). Hat sich bis zum Tipp etwas geändert, lehnt der
Helfer ab, statt etwas anderes zu tun als das, was Eike gesehen hat.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from kern import config, konnektoren

KONNEKTOR = "pc"
PORT = 8765
GILT_S = 600                      # eine Rückfrage verfällt nach zehn Minuten
MAX_OFFEN = 12
MAX_VERLAUF = 60


class PcFehler(Exception):
    """Der PC ist nicht erreichbar oder lehnt ab — der Text ist für Eike gedacht."""


def _ziel() -> tuple[str, str]:
    z = konnektoren.zugang(KONNEKTOR)
    if not z:
        raise PcFehler("Für den PC fehlen Adresse und Schlüssel — Freigaben → PC → Einrichten.")
    adresse, schluessel = z
    adresse = adresse.strip().rstrip("/")
    if "://" not in adresse:
        adresse = "http://" + adresse
    if adresse.count(":") < 2:                                  # kein Port angegeben
        adresse += f":{PORT}"
    return adresse, schluessel


async def _anfrage(methode: str, pfad: str, daten: dict | None = None, timeout: float = 60) -> httpx.Response:
    adresse, schluessel = _ziel()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=4)) as c:
            r = await c.request(methode, adresse + pfad, json=daten,
                                headers={"Authorization": f"Bearer {schluessel}"})
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise PcFehler("Der PC ist nicht erreichbar — er ist aus, schläft, oder der Friday-Helfer läuft nicht.") from None
    except httpx.TimeoutException:
        raise PcFehler("Der PC antwortet nicht rechtzeitig.") from None
    if r.status_code == 401:
        raise PcFehler("Der PC lehnt Friday ab (Schlüssel passt nicht). Freigaben → PC → Einrichten, "
                       "dann den Befehl auf dem PC noch einmal ausführen.")
    return r


HELFER_DATEI = Path(__file__).resolve().parents[2] / "pc-helfer" / "friday_pc.py"


def helfer_version() -> str:
    """Die Version des Helfers, den dieser Friday-Stand mitbringt (steht als VERSION in der Datei)."""
    try:
        m = re.search(r'^VERSION = "([0-9.]+)"', HELFER_DATEI.read_text(encoding="utf-8"), re.M)
    except OSError:
        return ""
    return m.group(1) if m else ""


def _als_zahlen(v) -> tuple:
    try:
        return tuple(int(t) for t in str(v).split("."))
    except ValueError:
        return ()


def helfer_neuer(laeuft: str | None, mitgebracht: str) -> bool:
    """Ist der mitgebrachte Helfer neuer als der auf dem PC? Ohne Angabe des PCs: nein (nichts behaupten)."""
    a, b = _als_zahlen(laeuft), _als_zahlen(mitgebracht)
    return bool(a and b and b > a)


async def status() -> dict:
    r = await _anfrage("GET", "/status", timeout=8)
    if r.status_code != 200:
        raise PcFehler(f"Der PC meldet einen Fehler ({r.status_code}).")
    return r.json()


async def lesen(pfad: str, fragen: dict | None = None, timeout: float = 20) -> dict:
    """Ein lesender Aufruf des Helfers für die PC-Seite (Messwerte, Fenster, Dateien …). Fehler des Helfers als Text."""
    query = ("?" + urlencode({k: v for k, v in (fragen or {}).items() if v})) if any((fragen or {}).values()) else ""
    r = await _anfrage("GET", pfad + query, timeout=timeout)
    try:
        d = r.json()
    except ValueError:
        raise PcFehler(f"Der PC antwortet unverständlich ({r.status_code}).") from None
    if r.status_code == 404:
        raise PcFehler("Das kann der Helfer auf dem PC noch nicht — bitte den Helfer aktualisieren (Freigaben → PC → Einrichten).")
    if r.status_code != 200 or not isinstance(d, dict) or d.get("ok") is False:
        raise PcFehler(str((d or {}).get("text") if isinstance(d, dict) else "") or f"Der PC meldet einen Fehler ({r.status_code}).")
    return d


async def medien(mit_bild: bool = False) -> dict | None:
    """Die aktuelle Mediensitzung am PC (Titel, Stand, optional Vorschaubild) — oder None."""
    r = await _anfrage("GET", "/medien?bild=1" if mit_bild else "/medien", timeout=15 if mit_bild else 8)
    if r.status_code == 404:                        # älterer Helfer ohne Medien
        return None
    if r.status_code != 200:
        raise PcFehler(f"Der PC meldet einen Fehler ({r.status_code}).")
    m = r.json().get("medien")
    return m if isinstance(m, dict) else None


async def bildschirm_jpeg(kante: int = 1600) -> bytes:
    """Der Bildschirm als kleines JPEG für das Modell (Gemini braucht kein Vollbild; ein 4K-PNG ist zu groß)."""
    import io
    from PIL import Image

    roh = await bildschirm()
    bild = Image.open(io.BytesIO(roh)).convert("RGB")
    bild.thumbnail((kante, kante))
    puffer = io.BytesIO()
    bild.save(puffer, format="JPEG", quality=80)
    return puffer.getvalue()


async def bildschirm() -> bytes:
    r = await _anfrage("GET", "/bildschirm", timeout=20)
    if r.status_code != 200:
        try:
            text = r.json().get("text")
        except ValueError:
            text = None
        raise PcFehler(text or f"Kein Bildschirmfoto ({r.status_code}).")
    return r.content


async def aktion(art: str, angaben: dict | None = None, probe: bool = False, bestaetigt: bool = False,
                 erwartet: str | None = None) -> dict:
    """Eine Aktion auf dem PC. `bestaetigt` setzt NUR `entscheiden` (Eikes Tipp)."""
    daten = {"art": art, "angaben": angaben or {}, "probe": probe, "bestaetigt": bestaetigt}
    if erwartet is not None:
        daten["erwartet"] = erwartet
    r = await _anfrage("POST", "/aktion", daten, timeout=15 if probe else 90)
    try:
        antwort = r.json()
    except ValueError:
        raise PcFehler(f"Der PC antwortet unverständlich ({r.status_code}).") from None
    if not isinstance(antwort, dict):
        raise PcFehler("Der PC antwortet unverständlich.")
    return antwort


# ---------- Rückfragen ----------

_offen: dict[str, dict] = {}
_sperre = threading.Lock()
_datei_sperre = threading.Lock()


def _aufraeumen() -> None:
    jetzt = time.time()
    for rid in [r for r, e in _offen.items() if jetzt - e["zeit"] > GILT_S]:
        e = _offen.pop(rid)
        _verlauf_neu(e["beschreibung"], "verfallen", "Nicht bestätigt — verfallen.", e["art"])


def vormerken(art: str, angaben: dict, beschreibung: str, fingerabdruck: str, fremd: bool = False) -> dict:
    """Eine heikle Aktion wartet auf Eikes Tipp. Ausgeführt wird hier NICHTS."""
    with _sperre:
        _aufraeumen()
        while len(_offen) >= MAX_OFFEN:
            _offen.pop(min(_offen, key=lambda r: _offen[r]["zeit"]))
        rid = secrets.token_hex(8)
        _offen[rid] = {"id": rid, "art": art, "angaben": dict(angaben), "beschreibung": beschreibung,
                       "fingerabdruck": fingerabdruck, "fremd": bool(fremd), "zeit": time.time()}
        return _oeffentlich(_offen[rid])


def _oeffentlich(e: dict) -> dict:
    """Was die App sieht: kein Fingerabdruck, keine rohen Angaben."""
    return {"id": e["id"], "beschreibung": e["beschreibung"], "fremd": e["fremd"],
            "rest_s": max(0, int(GILT_S - (time.time() - e["zeit"])))}


def offene() -> list[dict]:
    with _sperre:
        _aufraeumen()
        return [_oeffentlich(e) for e in sorted(_offen.values(), key=lambda e: e["zeit"])]


async def entscheiden(rid: str, ja: bool) -> dict:
    """Eikes Tipp auf „Ausführen“ oder „Ablehnen“. Jede Rückfrage gilt genau einmal."""
    with _sperre:
        _aufraeumen()
        e = _offen.pop(rid, None)
    if e is None:
        return {"ok": False, "text": "Diese Rückfrage gibt es nicht mehr — abgelaufen oder schon entschieden."}
    if not ja:
        _verlauf_neu(e["beschreibung"], "abgelehnt", "Abgelehnt — nichts passiert.", e["art"])
        return {"ok": True, "text": "Abgelehnt — nichts passiert."}
    # Das Schreibrecht kann inzwischen weg sein: dann gilt auch der Tipp nicht.
    if not (konnektoren.benutzbar(KONNEKTOR) and konnektoren.schreiben_erlaubt(KONNEKTOR)):
        _verlauf_neu(e["beschreibung"], "fehler", "Freigabe inzwischen aus — nichts ausgeführt.", e["art"])
        return {"ok": False, "text": "Die Freigabe für den PC ist inzwischen aus — nichts ausgeführt."}
    try:
        r = await aktion(e["art"], e["angaben"], bestaetigt=True, erwartet=e["fingerabdruck"])
    except PcFehler as exc:
        _verlauf_neu(e["beschreibung"], "fehler", str(exc), e["art"])
        return {"ok": False, "text": str(exc)}
    ok = bool(r.get("ok"))
    text = str(r.get("text") or ("Erledigt." if ok else "Nicht ausgeführt."))
    _verlauf_neu(e["beschreibung"], "bestaetigt" if ok else "fehler", text, e["art"])
    return {"ok": ok, "text": text}


# ---------- Verlauf: was am PC passiert ist ----------

def _verlauf_datei():
    return config.daten_ordner() / "pc_verlauf.json"


def verlauf(anzahl: int = 20) -> list[dict]:
    try:
        liste = json.loads(_verlauf_datei().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return liste[-anzahl:][::-1]


def _verlauf_neu(beschreibung: str, wie: str, ergebnis: str, art: str = "") -> None:
    """`wie`: direkt | bestaetigt | abgelehnt | verfallen | fehler. Nur Aktionen, nie Gelesenes."""
    with _datei_sperre:
        _verlauf_schreiben(beschreibung, wie, ergebnis, art)


def _verlauf_schreiben(beschreibung: str, wie: str, ergebnis: str, art: str) -> None:
    ziel = _verlauf_datei()
    try:
        liste = json.loads(ziel.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        liste = []
    liste = (liste + [{"zeit": config.jetzt().isoformat(timespec="seconds"), "art": art, "wie": wie,
                       "beschreibung": beschreibung[:300], "ergebnis": ergebnis[:300]}])[-MAX_VERLAUF:]
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False, indent=1)
    os.replace(neu, ziel)


def vermerken(beschreibung: str, ergebnis: str, art: str = "", ok: bool = True) -> None:
    """Eine sichere Aktion, die gleich lief — damit der Verlauf vollständig ist."""
    _verlauf_neu(beschreibung, "direkt" if ok else "fehler", ergebnis, art)


# ---------- Einrichten ----------

_codes: dict[str, dict] = {}
CODE_GILT_S = 1800


def einrichten(adresse: str, neu: bool = False) -> str:
    """Adresse speichern, Schlüssel erzeugen (oder behalten) — und einen Einmal-Code,
    mit dem der PC sich den Installer samt Schlüssel holt."""
    adresse = (adresse or "").strip()
    if not adresse or len(adresse) > 200 or any(z.isspace() for z in adresse):
        raise PcFehler("Die Adresse des PCs fehlt (z. B. 100.64.0.20:8765).")
    alt = konnektoren.zugang(KONNEKTOR)
    schluessel = alt[1] if alt and not neu else secrets.token_urlsafe(32)
    konnektoren.zugang_setzen(KONNEKTOR, adresse, schluessel)
    code = secrets.token_urlsafe(18)
    jetzt = time.time()
    for c in [c for c, e in _codes.items() if jetzt - e["zeit"] > CODE_GILT_S]:
        _codes.pop(c)
    _codes[code] = {"zeit": jetzt}
    return code


def code_einloesen(code: str) -> str | None:
    """Gibt den Schlüssel heraus — genau einmal, und nur innerhalb von 30 Minuten."""
    e = _codes.pop(code or "", None)
    if not e or time.time() - e["zeit"] > CODE_GILT_S:
        return None
    z = konnektoren.zugang(KONNEKTOR)
    return z[1] if z else None
